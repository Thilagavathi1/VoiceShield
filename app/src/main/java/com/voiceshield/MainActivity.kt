package com.voiceshield

import android.Manifest
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.lifecycle.viewmodel.compose.viewModel
import com.voiceshield.guard.GuardScreen
import com.voiceshield.guard.GuardService
import com.voiceshield.guard.GuardViewModel
import com.voiceshield.ui.theme.VoiceShieldTheme

class MainActivity : ComponentActivity() {

    private val permissions = buildList {
        add(Manifest.permission.RECORD_AUDIO)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            add(Manifest.permission.POST_NOTIFICATIONS)
        }
    }.toTypedArray()

    private val requestPermissions =
        registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {}

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        requestPermissions.launch(permissions)

        setContent {
            VoiceShieldTheme {
                val vm: GuardViewModel = viewModel()
                val ui by vm.ui.collectAsState()

                GuardScreen(
                    ui = ui,
                    onStart = {
                        // Start the foreground service FIRST: it must be created while the
                        // app is still visible, or background mic capture dies silently on
                        // Android 14+ the moment the elder returns to the dialer.
                        GuardService.start(this)
                        vm.startGuard()
                    },
                    onStop = {
                        vm.stopGuard()
                        GuardService.stop(this)
                    },
                    // Debug builds surface risk/pattern/latency so judges can see the
                    // machinery; the elder's build shows none of it.
                    showDebug = BuildConfig.DEBUG,
                )
            }
        }
    }
}
