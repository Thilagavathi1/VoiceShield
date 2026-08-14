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

        engine = RtcEngine.create(config).apply {
            enableAudio()
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
