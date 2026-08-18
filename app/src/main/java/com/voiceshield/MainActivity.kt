package com.voiceshield

import android.Manifest
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.compose.LifecycleResumeEffect
import androidx.lifecycle.viewmodel.compose.viewModel
import com.voiceshield.guard.GuardScreen
import com.voiceshield.guard.GuardViewModel
import com.voiceshield.guard.call.CallScreeningSetup
import com.voiceshield.ui.theme.VoiceShieldTheme

class MainActivity : ComponentActivity() {

    private val permissions = buildList {
        add(Manifest.permission.RECORD_AUDIO)
        add(Manifest.permission.READ_PHONE_STATE)
        // Lets VoiceShield stay silent for family. Without it we guard every call.
        add(Manifest.permission.READ_CONTACTS)
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
                val context = LocalContext.current

                var autoProtectOn by remember {
                    mutableStateOf(CallScreeningSetup.isGranted(context))
                }

                // The role is granted in system Settings, so re-check on every resume
                // rather than trusting an activity result.
                LifecycleResumeEffect(Unit) {
                    autoProtectOn = CallScreeningSetup.isGranted(context)
                    onPauseOrDispose {}
                }

                val roleLauncher = rememberLauncherForActivityResult(
                    ActivityResultContracts.StartActivityForResult()
                ) {
                    autoProtectOn = CallScreeningSetup.isGranted(context)
                }

                GuardScreen(
                    ui = ui,
                    onStart = vm::startGuard,
                    onStop = vm::stopGuard,
                    // Debug builds surface risk/pattern/latency/trigger so judges can see
                    // the machinery; the elder's build shows none of it.
                    showDebug = BuildConfig.DEBUG,
                    autoProtectOn = autoProtectOn,
                    onEnableAutoProtect = CallScreeningSetup.requestIntent(context)?.let { intent ->
                        { roleLauncher.launch(intent) }
                    },
                )
            }
        }
    }
}
