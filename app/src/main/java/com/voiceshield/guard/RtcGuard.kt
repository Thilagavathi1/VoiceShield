package com.voiceshield.guard

import android.content.Context
import android.util.Log
import io.agora.rtc2.ChannelMediaOptions
import io.agora.rtc2.Constants
import io.agora.rtc2.IRtcEngineEventHandler
import io.agora.rtc2.RtcEngine
import io.agora.rtc2.RtcEngineConfig

/**
 * Owns the Agora RTC engine: publishes the phone's microphone into the channel that the
 * Conversational AI guard agent is subscribed to.
 *
 * Audio routing note that matters more than it looks: the elder puts the suspicious call
 * on SPEAKER, and we capture both voices through the mic. So we must NOT let Agora treat
 * the far-end (scammer, coming out of our own loudspeaker) as echo and cancel it away --
 * that voice is the thing we are trying to analyse. Hence the deliberately unusual audio
 * scenario below.
 */
class RtcGuard(
    private val context: Context,
    private val onAgentJoined: () -> Unit = {},
    private val onError: (String) -> Unit = {},
) {

    private var engine: RtcEngine? = null

    private val handler = object : IRtcEngineEventHandler() {
        override fun onJoinChannelSuccess(channel: String?, uid: Int, elapsed: Int) {
            Log.i(TAG, "joined $channel as $uid in ${elapsed}ms")
        }

        override fun onUserJoined(uid: Int, elapsed: Int) {
            // The only other participant we subscribe to is the guard agent.
            Log.i(TAG, "agent joined: $uid")
            onAgentJoined()
        }

        override fun onError(err: Int) {
            Log.e(TAG, "rtc error $err")
            onError("RTC error $err")
        }
    }

    fun start(session: BackendClient.Session) {
        if (engine != null) return

        val config = RtcEngineConfig().apply {
            mContext = context
            mAppId = session.appId
            mEventHandler = handler
            // Chatroom scenario keeps AEC/ANS from aggressively suppressing the
            // loudspeaker-borne voice we need to hear.
            mAudioScenario = Constants.AUDIO_SCENARIO_CHATROOM
        }

        // Diagnostic probe: RtcEngine.create() swallows native-init failures and returns
        // null with no log of its own, so load the .so files ourselves to surface the
        // real linker error.
        for (lib in listOf("aosl", "agora-rtc-sdk")) {
            try {
                System.loadLibrary(lib)
                Log.i(TAG, "loadLibrary($lib) OK")
            } catch (t: Throwable) {
                Log.e(TAG, "loadLibrary($lib) FAILED: ${t.javaClass.simpleName}: ${t.message}")
            }
        }

        // RtcEngine.create() returns null on failure rather than throwing, so the
        // original `create(config).apply { ... }` blew up with an NPE on enableAudio()
        // and hid the real cause. Capture both failure modes and report them.
        val created = try {
            RtcEngine.create(config)
        } catch (t: Throwable) {
            Log.e(TAG, "RtcEngine.create threw", t)
            onError("RtcEngine.create threw: ${t.javaClass.simpleName}: ${t.message}")
            null
        }
        // Fall back to the 3-arg overload. The config-based create() returns null on this
        // device while emitting no log of its own, and the simpler overload skips
        // RtcEngineConfig entirely -- including mAudioScenario, the one non-default field
        // we set. If this path works, the scenario or the config object is the culprit.
        val engineOrNull = created ?: try {
            Log.w(TAG, "config-based create() returned null; trying 3-arg overload")
            RtcEngine.create(context, session.appId, handler)
        } catch (t: Throwable) {
            Log.e(TAG, "3-arg create threw", t)
            null
        }

        if (engineOrNull == null) {
            Log.e(TAG, "both create() paths failed (appId len=${session.appId.length})")
            onError("Agora engine could not start (appId len=${session.appId.length})")
            return
        }
        Log.i(TAG, "engine created via ${if (created != null) "config" else "3-arg"}")

        engine = engineOrNull.apply {
            enableAudio()
            // Set the scenario on the engine rather than trusting RtcEngineConfig: the
            // working path on real devices is the 3-arg create(), which never sees the
            // config object -- so mAudioScenario silently reverts to default there, and
            // default AEC/ANS cancels the loudspeaker-borne scammer voice we exist to
            // hear. This call is the same on both paths, so it is safe to make always.
            setAudioScenario(Constants.AUDIO_SCENARIO_CHATROOM)
            // We publish; we do not want the agent's own audio processed as our input.
            setDefaultAudioRoutetoSpeakerphone(true)

            val options = ChannelMediaOptions().apply {
                publishMicrophoneTrack = true
                autoSubscribeAudio = true   // hear the guard's warning
                clientRoleType = Constants.CLIENT_ROLE_BROADCASTER
                channelProfile = Constants.CHANNEL_PROFILE_LIVE_BROADCASTING
            }

            val ret = joinChannel(
                session.rtcToken,
                session.channel,
                session.uid.toIntOrNull() ?: 0,
                options,
            )
            if (ret != 0) onError("joinChannel failed: $ret")
        }
    }

    fun stop() {
        engine?.leaveChannel()
        engine = null
        RtcEngine.destroy()
    }

    private companion object {
        const val TAG = "VoiceShieldRtc"
    }
}
