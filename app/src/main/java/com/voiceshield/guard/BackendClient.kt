package com.voiceshield.guard

import android.util.Log
import com.voiceshield.BuildConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * Talks to the VoiceShield backend. Deliberately the only place that knows about the network.
 *
 * The app never sees the Agora App Certificate or Customer Secret; it receives a
 * short-lived RTC token for one channel and nothing else.
 */
class BackendClient(private val baseUrl: String = BuildConfig.BACKEND_URL) {

    private val http = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        // No read timeout kill on the websocket; ping keeps it alive instead.
        .pingInterval(20, TimeUnit.SECONDS)
        .build()

    private val json = "application/json".toMediaType()

    data class Session(
        val channel: String,
        val appId: String,
        val uid: String,
        val rtcToken: String,
        val agentId: String,
    )

    suspend fun startSession(channel: String, language: String): Session =
        withContext(Dispatchers.IO) {
            val body = JSONObject()
                .put("channel", channel)
                .put("language", language)
                .toString()
                .toRequestBody(json)

            val req = Request.Builder()
                .url("$baseUrl/session/start")
                .post(body)
                .build()

            http.newCall(req).execute().use { res ->
                val text = res.body?.string().orEmpty()
                if (!res.isSuccessful) {
                    throw BackendException(res.code, text)
                }
                val o = JSONObject(text)
                Session(
                    channel = o.getString("channel"),
                    appId = o.getString("app_id"),
                    uid = o.getString("uid"),
                    rtcToken = o.getString("rtc_token"),
                    agentId = o.getString("agent_id"),
                )
            }
        }

    /** Returns the session summary, including the evidence transcript. */
    suspend fun stopSession(channel: String): String = withContext(Dispatchers.IO) {
        val req = Request.Builder()
            .url("$baseUrl/session/$channel/stop")
            .post(ByteArray(0).toRequestBody(json))
            .build()
        runCatching {
            http.newCall(req).execute().use { it.body?.string().orEmpty() }
        }.getOrElse {
            Log.w(TAG, "stopSession failed", it)
            ""
        }
    }

    /**
     * Live risk updates. Emits every time the backend scores a conversation turn.
     *
     * This is VoiceShield's own computation rather than conversation content, which is why it
     * comes over our socket instead of Agora Signaling. Transcripts arrive separately via
     * the Agora client toolkit.
     */
    fun riskUpdates(channel: String): Flow<RiskUpdate> = callbackFlow {
        val wsUrl = baseUrl.replaceFirst("http", "ws")
        val req = Request.Builder().url("$wsUrl/ws/$channel").build()

        val listener = object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                runCatching { RiskUpdate.fromJson(JSONObject(text)) }
                    .onSuccess { if (it != null) trySend(it) }
                    .onFailure { Log.w(TAG, "bad risk payload: $text", it) }
            }

            override fun onFailure(ws: WebSocket, t: Throwable, response: Response?) {
                Log.w(TAG, "risk socket failed", t)
                close(t)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                close()
            }
        }

        val socket = http.newWebSocket(req, listener)
        awaitClose { socket.cancel() }
    }

    class BackendException(val code: Int, val body: String) :
        Exception("backend $code: ${body.take(300)}")

    private companion object {
        const val TAG = "VoiceShieldBackend"
    }
}

/**
 * One scored conversation turn.
 *
 * [error] non-null means the backend could not judge this turn at all (classifier
 * outage, no API credit, unparseable output). That is NOT the same as "safe", and the
 * UI must never render it as protection — see GuardState.DEGRADED.
 */
data class RiskUpdate(
    val risk: Int,
    val pattern: String,
    val signals: List<String>,
    val latencyMs: Int,
    val warned: Boolean,
    val error: String?,
) {
    val usable: Boolean get() = error == null

    companion object {
        fun fromJson(o: JSONObject): RiskUpdate? {
            if (o.optString("type") != "risk") return null
            val arr = o.optJSONArray("signals")
            return RiskUpdate(
                risk = o.optInt("risk"),
                pattern = o.optString("pattern", "none"),
                signals = buildList {
                    for (i in 0 until (arr?.length() ?: 0)) add(arr!!.getString(i))
                },
                latencyMs = o.optInt("latency_ms"),
                warned = o.optBoolean("warned"),
                // isNull() matters: optString would turn JSON null into "null".
                error = if (o.isNull("error")) null else o.optString("error").ifBlank { null },
            )
        }
    }
}
