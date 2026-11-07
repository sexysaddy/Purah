import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import Quickshell.Hyprland

// Purah's interaction surface lives in the Wayland overlay layer, not as a
// Hyprland toplevel. This keeps it above the 3D desktop/window scene.
ShellRoot {
    id: shell

    readonly property string appDir: Quickshell.env("PURAH_APP_DIR") || ""
    readonly property string runtimeDir: Quickshell.env("PURAH_RUNTIME_DIR") || (Quickshell.env("XDG_RUNTIME_DIR") + "/purah")
    readonly property string statePath: runtimeDir + "/ui-state.json"
    readonly property string commandPath: runtimeDir + "/ui-command.json"
    readonly property string readyPath: runtimeDir + "/quickshell-ready"

    property var ui: ({
        visible: false,
        expanded: false,
        mode: "CONNECTED",
        status: "PURAH // READY",
        title: "SHEIKAH LINK // READY",
        transcript: "",
        input_enabled: true,
        input_mode: "voice",
        revision: 0
    })
    property int nextCommand: 0
    property int localDismissAt: 0
    property real eyeProgress: 0
    property real pulseLevel: 0.82
    property real glitchStrength: 0.0
    property real glitchOffset: 6.0
    property real glitchSliceOneY: 58.0
    property real glitchSliceTwoY: 124.0
    property real glitchSliceHeight: 10.0
    // Compact bottom-left companion. The conversation panel unfolds above her head,
    // rather than expanding into a centered desktop-sized window.
    property real hudLayoutProgress: 0.0
    property int dialogRevealColumns: 0
    readonly property int dialogTotalColumns: 64
    readonly property real dialogHeight: 238
    property real dialogScanY: 12
    property real dialogScanAlpha: 0.0
    property real dialogEdgePulse: 0.0
    property bool dismissAfterAnimation: false

    // Full-body sprites sliced from the supplied sheet. Idle/listening uses the
    // eye-blink strip; only actual TTS playback uses phoneme mouth shapes.
    readonly property var blinkFrameFiles: [
        "purah_blink_open.png",
        "purah_blink_half_start.png",
        "purah_blink_closed.png",
        "purah_blink_half_end.png",
        "purah_blink_smile_closed.png",
        "purah_blink_squint.png"
    ]
    readonly property var phonemeFrameFiles: [
        "purah_phoneme_neutral.png",
        "purah_phoneme_a_ya.png",
        "purah_phoneme_o_yo.png",
        "purah_phoneme_u_yu.png",
        "purah_phoneme_i_y.png",
        "purah_phoneme_e_ye.png",
        "purah_phoneme_m_b_p.png",
        "purah_phoneme_f_v.png",
        "purah_phoneme_t_d_n.png",
        "purah_phoneme_k_g_h.png",
        "purah_phoneme_s_z.png",
        "purah_phoneme_sh_zh_shch_ch.png",
        "purah_phoneme_l_r.png",
        "purah_phoneme_y.png",
        "purah_phoneme_transition.png",
        "purah_phoneme_smile.png"
    ]
    property int blinkFrameIndex: 0
    property var speechFrameSequence: []
    property int speechFrameCursor: 0
    property int currentPhonemeFrame: 0
    property string speechSequenceSource: ""
    property bool lastVisible: false

    function assetUrl(name) {
        if (appDir.length > 0) return "file://" + appDir + "/assets/" + name
        return Qt.resolvedUrl(name)
    }

    function purahSpriteForMode(mode) {
        if (mode === "SPEAKING") {
            var phoneme = Math.max(0, Math.min(currentPhonemeFrame, phonemeFrameFiles.length - 1))
            return assetUrl(phonemeFrameFiles[phoneme])
        }
        var blink = Math.max(0, Math.min(blinkFrameIndex, blinkFrameFiles.length - 1))
        return assetUrl(blinkFrameFiles[blink])
    }

    function latestAssistantUtterance(value) {
        var lines = String(value || "").split(/\r?\n/)
        var active = false
        var current = ""
        var latest = ""
        for (var i = 0; i < lines.length; ++i) {
            var line = lines[i]
            var assistant = line.match(/^\s*(?:PURAH|ASSISTANT)\s*>\s*(.*)$/i)
            if (assistant) {
                active = true
                current = assistant[1]
                latest = current
                continue
            }
            if (/^\s*YOU\s*>/i.test(line)) {
                active = false
                current = ""
                continue
            }
            if (active) {
                current += (current.length ? "\n" : "") + line
                latest = current
            }
        }
        return String(latest || "").trim()
    }

    function phonemeFrameForCharacter(character) {
        var c = String(character || "").toLowerCase()
        if ("аяa".indexOf(c) >= 0) return 1
        if ("оёo".indexOf(c) >= 0) return 2
        if ("уюu".indexOf(c) >= 0) return 3
        if ("иыiy".indexOf(c) >= 0) return 4
        if ("эеe".indexOf(c) >= 0) return 5
        if ("мбпmbp".indexOf(c) >= 0) return 6
        if ("фвfv".indexOf(c) >= 0) return 7
        if ("тднtdn".indexOf(c) >= 0) return 8
        if ("кгхkgh".indexOf(c) >= 0) return 9
        if ("сзsz".indexOf(c) >= 0) return 10
        if ("шжщч".indexOf(c) >= 0 || "j" === c) return 11
        if ("лрlr".indexOf(c) >= 0) return 12
        if ("й" === c) return 13
        if ("ьъ'’".indexOf(c) >= 0) return 14
        if ("!?".indexOf(c) >= 0) return 15
        return 0
    }

    function appendSpeechFrames(text) {
        var source = String(text || "").toLowerCase()
        for (var i = 0; i < source.length; ++i) {
            var c = source.charAt(i)
            if (/\s/.test(c)) continue
            if (/[.!?…,:;—-]/.test(c)) {
                // Tiny pauses and a smiling finish make punctuation read naturally.
                if (c === "!" || c === "?") speechFrameSequence.push(15)
                speechFrameSequence.push(14)
                speechFrameSequence.push(0)
                continue
            }
            speechFrameSequence.push(phonemeFrameForCharacter(c))
        }
    }

    function updateSpeechFrameSequence() {
        if (ui.mode !== "SPEAKING") return
        var utterance = latestAssistantUtterance(ui.transcript)
        if (!utterance) return
        if (speechSequenceSource.length > 0 && utterance.indexOf(speechSequenceSource) !== 0) {
            speechFrameSequence = []
            speechFrameCursor = 0
            currentPhonemeFrame = 0
            speechSequenceSource = ""
        }
        if (utterance.length > speechSequenceSource.length) {
            appendSpeechFrames(utterance.substring(speechSequenceSource.length))
            speechSequenceSource = utterance
        }
    }

    function advanceBlink() {
        if (!ui.visible || ui.mode === "SPEAKING") {
            blinkFrameIndex = 0
            return
        }
        if (blinkFrameIndex === 0) {
            var roll = Math.random()
            if (roll < 0.045) {
                blinkFrameIndex = 4
                blinkTimer.interval = 135
            } else if (roll < 0.09) {
                blinkFrameIndex = 5
                blinkTimer.interval = 155
            } else {
                blinkFrameIndex = 1
                blinkTimer.interval = 75
            }
        } else if (blinkFrameIndex === 1) {
            blinkFrameIndex = 2
            blinkTimer.interval = 85
        } else if (blinkFrameIndex === 2) {
            blinkFrameIndex = 3
            blinkTimer.interval = 75
        } else {
            blinkFrameIndex = 0
            blinkTimer.interval = 1900 + Math.floor(Math.random() * 3000)
        }
        blinkTimer.restart()
    }

    function statusLabelForMode(mode) {
        if (mode === "LISTENING") return "СЛУШАЮ"
        if (mode === "TRANSCRIBING") return "РАСПОЗНАЮ РЕЧЬ"
        if (mode === "THINKING") return "ДУМАЮ"
        if (mode === "VISION") return "СМОТРЮ НА ЭКРАН"
        if (mode === "TYPING") return "ГОТОВЛЮ ОТВЕТ"
        if (mode === "SPEAKING") return "ОТВЕЧАЮ"
        if (mode === "ERROR") return "ОШИБКА СВЯЗИ"
        if (String(shell.ui.input_mode || "voice") === "text") return "РЕЖИМ ТЕКСТА"
        return "ГОТОВА"
    }

    function transcriptEntries(value) {
        var source = String(value || "").trim()
        if (!source) return [{ speaker: "assistant", message: "Скажи, что нужно сделать…" }]
        var lines = source.split(/\n+/)
        var result = []
        for (var i = 0; i < lines.length; ++i) {
            var line = lines[i].trim()
            if (!line) continue
            var match = line.match(/^(YOU\s*>\s*|PURAH\s*>\s*|ASSISTANT\s*>\s*)/i)
            if (match) {
                result.push({ speaker: /^YOU/i.test(match[0]) ? "user" : "assistant", message: line.slice(match[0].length) })
            } else {
                result.push({ speaker: "system", message: line })
            }
        }
        return result.length ? result : [{ speaker: "assistant", message: source }]
    }

    // State files are polled frequently for heartbeat updates. Mutate only changed
    // message roles/text, rather than rebuilding the entire Repeater on every poll;
    // this prevents the conversation from flickering while tokens are streamed.
    function syncTranscript(value) {
        var source = String(value || "")
        if (source === lastTranscriptText) return
        lastTranscriptText = source
        var entries = transcriptEntries(source)
        while (transcriptModel.count > entries.length) transcriptModel.remove(transcriptModel.count - 1)
        for (var i = 0; i < entries.length; ++i) {
            if (i >= transcriptModel.count) {
                transcriptModel.append(entries[i])
            } else {
                var current = transcriptModel.get(i)
                if (current.speaker !== entries[i].speaker) transcriptModel.setProperty(i, "speaker", entries[i].speaker)
                if (current.message !== entries[i].message) transcriptModel.setProperty(i, "message", entries[i].message)
            }
        }
    }
    property bool receivedBackendState: false
    property string lastMode: "CONNECTED"
    readonly property double shellStartedAt: Date.now()
    property string lastTranscriptText: ""

    ListModel {
        id: transcriptModel
        dynamicRoles: true
        ListElement { speaker: "assistant"; message: "Скажи, что нужно сделать…" }
    }

    onUiChanged: {
        var currentMode = String(ui.mode || "CONNECTED")
        var currentlyVisible = !!ui.visible
        if (currentMode !== lastMode) {
            if (currentMode === "SPEAKING" && lastMode !== "SPEAKING") {
                responseFlash.restart()
                blinkFrameIndex = 0
                speechFrameSequence = []
                speechFrameCursor = 0
                currentPhonemeFrame = 0
                speechSequenceSource = ""
                updateSpeechFrameSequence()
            } else if (lastMode === "SPEAKING" && currentMode !== "SPEAKING") {
                speechFrameSequence = []
                speechFrameCursor = 0
                currentPhonemeFrame = 0
                speechSequenceSource = ""
                blinkFrameIndex = 0
                blinkTimer.interval = 1800 + Math.floor(Math.random() * 3000)
            }
            if (bubbleFrame) bubbleFrame.requestPaint()
            if (companionFrame) companionFrame.requestPaint()
        }
        if (currentlyVisible !== lastVisible) {
            blinkFrameIndex = 0
            if (currentlyVisible && currentMode !== "SPEAKING") {
                blinkTimer.interval = 1800 + Math.floor(Math.random() * 3000)
            }
            lastVisible = currentlyVisible
        }
        if (currentMode === "SPEAKING") updateSpeechFrameSequence()
        lastMode = currentMode
    }

    function pickScreen() {
        var list = Quickshell.screens
        var mon = Hyprland.focusedMonitor
        for (var i = 0; i < list.length; ++i) {
            if (mon && list[i].name === mon.name) return list[i]
        }
        return list.length ? list[0] : null
    }

    function readState() {
        try {
            var raw = stateFile.text()
            if (!raw || !raw.trim()) return
            var incoming = JSON.parse(raw)
            if (localDismissAt && Date.now() - localDismissAt < 900 && incoming.visible) return
            var incomingHeartbeat = Number(incoming.heartbeat_ms || 0)
            // Do not resurrect a stale visible overlay from the previous run while
            // the Python backend is still starting and has not published a fresh heartbeat.
            if (!receivedBackendState && incomingHeartbeat > 0 && Date.now() - incomingHeartbeat > 6500) return
            var wasVisible = !!ui.visible
            var wasExpanded = !!ui.expanded
            ui = Object.assign({}, ui, incoming)
            syncTranscript(incoming.transcript || "")
            if (incomingHeartbeat > 0) receivedBackendState = true
            if (!wasVisible && ui.visible) {
                eyeProgress = 0
                glitchStrength = 1.0
                glitchFade.restart()
                riseAnimation.restart()
            } else if (wasVisible && !ui.visible) {
                dialogOpenAnimation.stop()
                dialogCloseAnimation.stop()
                eyeProgress = 0
                dialogRevealColumns = 0
                hudLayoutProgress = 0
            }
            if (!wasExpanded && ui.expanded) {
                dialogRevealColumns = 0
                dialogOpenAnimation.restart()
                // Focus the input after the reveal starts. Polling the state file must
                // not repeatedly call requestActivate and steal focus back from typing.
                Qt.callLater(function() {
                    if (shell.ui.visible && shell.ui.expanded && !inputField.activeFocus) {
                        overlay.requestActivate()
                        inputField.forceActiveFocus(Qt.MouseFocusReason)
                    }
                })
            } else if (wasExpanded && !ui.expanded) {
                dialogCloseAnimation.restart()
            }
        } catch (err) {
            // A read can race with the backend's atomic replace. The next poll retries.
        }
    }

    function dismissAnimated() {
        if (ui.visible && ui.expanded && hudLayoutProgress > 0.01) {
            dismissAfterAnimation = true
            dialogOpenAnimation.stop()
            dialogCloseAnimation.restart()
        } else {
            sendAction("dismiss")
        }
    }

    function sendAction(action, payload) {
        nextCommand += 1
        var command = {
            id: String(Date.now()) + "-" + String(nextCommand),
            action: action
        }
        if (payload !== undefined && payload !== null) command.payload = payload
        commandFile.setText(JSON.stringify(command) + "\n")
        if (action === "dismiss" || action === "quit") {
            dismissAfterAnimation = false
            localDismissAt = Date.now()
            dialogOpenAnimation.stop()
            dialogCloseAnimation.stop()
            ui = Object.assign({}, ui, { visible: false, expanded: false })
            dialogRevealColumns = 0
            hudLayoutProgress = 0
            eyeProgress = 0
        }
    }

    FileView {
        id: stateFile
        path: shell.statePath
        watchChanges: true
        printErrors: false
        onFileChanged: reload()
        onLoaded: shell.readState()
    }

    FileView {
        id: commandFile
        path: shell.commandPath
        printErrors: false
    }

    FileView {
        id: readyFile
        path: shell.readyPath
        printErrors: false
        // The launcher waits for this file before starting the Python backend.
        // Make the handshake write synchronous so it cannot race the poll loop.
        blockWrites: true
    }

    Timer {
        interval: shell.ui.visible ? 70 : 500
        repeat: true
        running: true
        onTriggered: {
            stateFile.reload()
            var heartbeat = Number(shell.ui.heartbeat_ms || 0)
            if (shell.receivedBackendState && heartbeat > 0 && Date.now() - heartbeat > 6500) {
                shell.ui = Object.assign({}, shell.ui, { visible: false, expanded: false })
                Qt.quit() // Backend died or user quit PURAH; don't leave a ghost overlay process.
            } else if (!shell.receivedBackendState && Date.now() - shell.shellStartedAt > 45000) {
                Qt.quit() // Backend never started; avoid leaving an orphaned invisible Quickshell process.
            }
        }
    }

    SequentialAnimation {
        id: responseFlash
        loops: 1
        PropertyAction { target: shell; property: "pulseLevel"; value: 0.12 }
        NumberAnimation { target: shell; property: "pulseLevel"; to: 1.0; duration: 180; easing.type: Easing.OutCubic }
        PauseAnimation { duration: 650 }
        NumberAnimation { target: shell; property: "pulseLevel"; to: 0.82; duration: 420; easing.type: Easing.OutCubic }
    }

    SequentialAnimation on pulseLevel {
        id: eyePulse
        running: shell.ui.visible && (shell.ui.mode === "LISTENING" || shell.ui.mode === "THINKING")
        loops: Animation.Infinite
        NumberAnimation { to: 0.22; duration: shell.ui.mode === "THINKING" ? 240 : 150; easing.type: Easing.InOutSine }
        NumberAnimation { to: 0.9; duration: shell.ui.mode === "THINKING" ? 240 : 150; easing.type: Easing.InOutSine }
    }

    // Natural idle/listening blink: open -> half -> closed -> half -> open.
    // Occasional smile/squint frames are deliberately rare so they read as expression,
    // not as a rapid looping GIF.
    Timer {
        id: blinkTimer
        interval: 2600
        repeat: false
        running: shell.ui.visible && shell.ui.mode !== "SPEAKING"
        onTriggered: shell.advanceBlink()
    }

    // The current text is converted to the closest available mouth shape. The TTS
    // backend does not expose per-phoneme timestamps, so this is a text-paced visual
    // approximation and never changes audio playback or assistant state.
    Timer {
        id: phonemeTimer
        interval: 105
        repeat: true
        running: shell.ui.visible && shell.ui.mode === "SPEAKING"
        onTriggered: {
            if (shell.speechFrameCursor < shell.speechFrameSequence.length) {
                shell.currentPhonemeFrame = shell.speechFrameSequence[shell.speechFrameCursor]
                shell.speechFrameCursor += 1
            } else {
                shell.currentPhonemeFrame = 0
            }
        }
    }

    NumberAnimation {
        id: riseAnimation
        target: shell
        property: "eyeProgress"
        from: 0
        to: 1
        duration: 320
        easing.type: Easing.OutCubic
    }

    NumberAnimation {
        id: glitchFade
        target: shell
        property: "glitchStrength"
        from: 1.0
        to: 0.0
        duration: 235
        easing.type: Easing.OutCubic
    }

    Timer {
        id: glitchTimer
        interval: 180
        repeat: true
        running: shell.ui.visible
        onTriggered: {
            // Short, clipped RGB-like tears: the sprite itself stays sharp, with no persistent ghost/shadow.
            var probability = shell.ui.mode === "THINKING" || shell.ui.mode === "VISION" ? 0.075
                           : shell.ui.mode === "SPEAKING" || shell.ui.mode === "TYPING" ? 0.060
                           : shell.ui.mode === "LISTENING" || shell.ui.mode === "TRANSCRIBING" ? 0.052 : 0.018
            if (Math.random() < probability) {
                shell.glitchStrength = 0.72 + Math.random() * 0.28
                shell.glitchOffset = 3 + Math.random() * 10
                shell.glitchSliceOneY = 28 + Math.random() * 65
                shell.glitchSliceTwoY = 98 + Math.random() * 48
                shell.glitchSliceHeight = 6 + Math.random() * 12
                glitchFade.restart()
            }
        }
    }

    SequentialAnimation {
        id: dialogOpenAnimation
        running: false
        PropertyAction { target: shell; property: "hudLayoutProgress"; value: 0 }
        PropertyAction { target: shell; property: "dialogRevealColumns"; value: 0 }
        PropertyAction { target: shell; property: "dialogScanAlpha"; value: 0 }
        PropertyAction { target: shell; property: "dialogEdgePulse"; value: 1.0 }
        ParallelAnimation {
            // A small, clean boot-up line and character settle before the full panel reveal.
            NumberAnimation { target: shell; property: "hudLayoutProgress"; from: 0; to: 1; duration: 235; easing.type: Easing.OutBack }
            NumberAnimation { target: shell; property: "dialogScanY"; from: 5; to: 17; duration: 235; easing.type: Easing.OutCubic }
            NumberAnimation { target: shell; property: "dialogEdgePulse"; from: 1.0; to: 0.48; duration: 235; easing.type: Easing.OutCubic }
        }
        ParallelAnimation {
            // The integer column count creates a fine segmented CRT wipe rather than a scale-in.
            NumberAnimation {
                target: shell; property: "dialogRevealColumns"
                from: 0; to: shell.dialogTotalColumns; duration: 535; easing.type: Easing.OutCubic
            }
            NumberAnimation {
                target: shell; property: "dialogScanY"
                from: 17; to: shell.dialogHeight - 12; duration: 485; easing.type: Easing.InOutSine
            }
            NumberAnimation { target: shell; property: "dialogEdgePulse"; from: 0.48; to: 0.0; duration: 535; easing.type: Easing.OutCubic }
            SequentialAnimation {
                NumberAnimation { target: shell; property: "dialogScanAlpha"; from: 0; to: 0.90; duration: 90; easing.type: Easing.OutCubic }
                PauseAnimation { duration: 65 }
                NumberAnimation { target: shell; property: "dialogScanAlpha"; to: 0; duration: 370; easing.type: Easing.OutCubic }
            }
        }
    }

    ParallelAnimation {
        id: dialogCloseAnimation
        running: false
        NumberAnimation {
            target: shell; property: "dialogRevealColumns"; to: 0
            duration: 250; easing.type: Easing.InCubic
        }
        NumberAnimation {
            target: shell; property: "hudLayoutProgress"; to: 0
            duration: 210; easing.type: Easing.InCubic
        }
        NumberAnimation { target: shell; property: "dialogEdgePulse"; to: 0; duration: 180; easing.type: Easing.InCubic }
        onFinished: {
            if (shell.dismissAfterAnimation) {
                shell.dismissAfterAnimation = false
                shell.sendAction("dismiss")
            }
        }
    }

    PanelWindow {
        id: overlay
        visible: shell.ui.visible
        screen: shell.pickScreen()
        color: "transparent"
        // Clicks outside the visible Purah HUD pass through to the application below.
        // The window is full-screen for positioning only; it must not be a click shield.
        mask: Region { item: interactionRegion }
        anchors {
            top: true
            bottom: true
            left: true
            right: true
        }
        exclusionMode: ExclusionMode.Ignore
        WlrLayershell.namespace: "purah-overlay"
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: shell.ui.visible ? WlrKeyboardFocus.OnDemand : WlrKeyboardFocus.None
        Component.onCompleted: readyFile.setText("ready\n")

        FocusScope {
            id: contentRoot
            anchors.fill: parent
            focus: shell.ui.visible && shell.ui.expanded

            Shortcut {
                sequence: "Escape"
                context: Qt.WindowShortcut
                enabled: shell.ui.visible
                onActivated: shell.dismissAnimated()
            }

            // Only the actual HUD area is part of the Wayland input region. The
            // rest of the fullscreen positioning surface passes clicks through.
            Item {
                id: interactionRegion
                x: hudGroup.x
                y: hudGroup.y + ((shell.ui.expanded || shell.hudLayoutProgress > 0.01) ? bubbleViewport.y : 0)
                width: (shell.ui.expanded || shell.hudLayoutProgress > 0.01)
                       ? Math.max(hudGroup.compactWidth, bubbleViewport.x + bubbleViewport.width)
                       : hudGroup.compactWidth
                height: (shell.ui.expanded || shell.hudLayoutProgress > 0.01)
                        ? hudGroup.compactHeight - bubbleViewport.y
                        : hudGroup.compactHeight
                opacity: 0
            }

            Item {
                id: hudGroup
                readonly property real compactWidth: 148
                readonly property real compactHeight: 208
                readonly property real expandedTargetWidth: Math.min(390, Math.max(350, contentRoot.width * 0.205))
                width: (shell.ui.expanded || shell.hudLayoutProgress > 0.01)
                       ? bubbleViewport.x + expandedTargetWidth : compactWidth
                height: compactHeight
                x: 24
                y: contentRoot.height - compactHeight - 18
                z: 2

                Item {
                    id: bubbleViewport
                    // A compact speech panel above Purah; it overlaps the top-right of her frame.
                    x: 116
                    y: -shell.dialogHeight + 18
                    width: shell.dialogRevealColumns > 0
                           ? Math.max(0, hudGroup.expandedTargetWidth * shell.dialogRevealColumns / shell.dialogTotalColumns)
                           : 0
                    height: shell.dialogHeight
                    clip: true
                    visible: shell.ui.visible && (shell.ui.expanded || shell.hudLayoutProgress > 0.01)
                    z: 1

                    // Fixed panel behind a moving clip region: CRT column wipe never paints outside the bubble.
                    Item {
                        id: bubble
                        x: 0
                        y: (1 - Math.min(1, shell.hudLayoutProgress)) * 7
                        width: hudGroup.expandedTargetWidth
                        height: shell.dialogHeight
                        opacity: Math.min(1, shell.hudLayoutProgress)
                        scale: 0.972 + Math.min(1, shell.hudLayoutProgress) * 0.028
                        transformOrigin: Item.BottomLeft
                        visible: shell.ui.visible && (shell.ui.expanded || shell.hudLayoutProgress > 0.01)

                // Angular HUD silhouette inspired by Capricorn's dialogue panel,
                // recolored to the Sheikah cyan/blue and muted gold palette.
                Canvas {
                    id: bubbleFrame
                    anchors.fill: parent
                    z: -1
                    onWidthChanged: requestPaint()
                    onHeightChanged: requestPaint()
                    onPaint: {
                        var ctx = getContext("2d")
                        ctx.clearRect(0, 0, width, height)
                        ctx.beginPath()
                        ctx.moveTo(0, 17)
                        ctx.lineTo(17, 0)
                        ctx.lineTo(width - 25, 0)
                        ctx.lineTo(width, 20)
                        ctx.lineTo(width, height - 16)
                        ctx.lineTo(width - 16, height)
                        ctx.lineTo(17, height)
                        ctx.lineTo(0, height - 17)
                        ctx.closePath()
                        ctx.fillStyle = "rgba(3, 10, 18, 0.975)"
                        ctx.fill()
                        if (shell.dialogEdgePulse > 0.01) {
                            ctx.lineWidth = 4.0
                            ctx.strokeStyle = "rgba(83, 229, 232, " + (0.17 * shell.dialogEdgePulse) + ")"
                            ctx.stroke()
                        }
                        ctx.lineWidth = 1.35
                        ctx.strokeStyle = shell.ui.mode === "THINKING" || shell.ui.mode === "VISION" ? "#d7b55e"
                                        : shell.ui.mode === "ERROR" ? "#f07979" : "#b99545"
                        ctx.stroke()

                        // Capricorn-like segmented header rail with Sheikah runes.
                        ctx.beginPath()
                        ctx.moveTo(17, 26)
                        ctx.lineTo(width - 20, 26)
                        ctx.strokeStyle = "rgba(83, 201, 213, 0.32)"
                        ctx.lineWidth = 1
                        ctx.stroke()
                        ctx.beginPath()
                        ctx.moveTo(19, 1)
                        ctx.lineTo(Math.min(width * 0.42, 150), 1)
                        ctx.strokeStyle = "#53e5e8"
                        ctx.lineWidth = 2
                        ctx.stroke()
                        ctx.beginPath()
                        ctx.moveTo(width - 27, 1)
                        ctx.lineTo(width - 10, 18)
                        ctx.strokeStyle = "#d7b55e"
                        ctx.lineWidth = 1.2
                        ctx.stroke()
                    }
                }

                // Consume clicks inside the panel without affecting anything beneath it.
                MouseArea { anchors.fill: parent; z: 0; onClicked: {} }

                Rectangle {
                    x: 5
                    y: shell.dialogScanY
                    width: Math.max(0, parent.width - 10)
                    height: 1
                    color: "#76efff"
                    opacity: shell.dialogScanAlpha
                    z: 0.5
                }

                Connections {
                    target: shell
                    function onUiChanged() { bubbleFrame.requestPaint() }
                    function onDialogEdgePulseChanged() { bubbleFrame.requestPaint() }
                }

                Column {
                    anchors.fill: parent
                    anchors.margins: 9
                    spacing: 6
                    z: 1

                    RowLayout {
                        width: parent.width
                        height: 34
                        spacing: 8
                        Image {
                            Layout.preferredWidth: 28
                            Layout.preferredHeight: 28
                            source: shell.assetUrl("sheikah_crest.svg")
                            fillMode: Image.PreserveAspectFit
                            smooth: true
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 1
                            Text {
                                text: "Purah"
                                color: "#e8c778"
                                font.family: "DejaVu Serif"
                                font.pixelSize: 17
                                font.italic: true
                                verticalAlignment: Text.AlignVCenter
                            }
                            Text {
                                text: "SHEIKAH // LINK"
                                color: "#75b7c1"
                                font.family: "DejaVu Sans Mono"
                                font.pixelSize: 8
                                font.letterSpacing: 0.7
                                verticalAlignment: Text.AlignVCenter
                            }
                        }
                        Item { Layout.fillWidth: true }
                        Rectangle {
                            id: closeButton
                            Layout.preferredWidth: 25
                            Layout.preferredHeight: 25
                            Layout.alignment: Qt.AlignVCenter
                            radius: 2
                            color: closeMouse.containsMouse ? "#28251d" : "#0b121a"
                            border.width: 1
                            border.color: closeMouse.containsMouse ? "#f1d38c" : "#8f793f"
                            Rectangle {
                                width: 10
                                height: 1.4
                                anchors.centerIn: parent
                                rotation: 45
                                color: closeMouse.containsMouse ? "#fff2c6" : "#e5c778"
                            }
                            Rectangle {
                                width: 10
                                height: 1.4
                                anchors.centerIn: parent
                                rotation: -45
                                color: closeMouse.containsMouse ? "#fff2c6" : "#e5c778"
                            }
                            MouseArea {
                                id: closeMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                onClicked: shell.dismissAnimated()
                            }
                        }
                    }

                    Rectangle {
                        width: parent.width
                        height: Math.max(96, parent.height - 34 - 34 - 12)
                        radius: 3
                        color: "#b9040b13"
                        border.width: 1
                        border.color: "#273d4b"

                        Flickable {
                            id: messageFlick
                            anchors.fill: parent
                            anchors.margins: 8
                            clip: true
                            contentWidth: width
                            contentHeight: messagesColumn.implicitHeight
                            boundsBehavior: Flickable.StopAtBounds
                            onContentHeightChanged: Qt.callLater(function() {
                                messageFlick.contentY = Math.max(0, messageFlick.contentHeight - messageFlick.height)
                            })

                            Column {
                                id: messagesColumn
                                width: messageFlick.width
                                spacing: 7

                                Repeater {
                                    model: transcriptModel
                                    delegate: Item {
                                        required property string speaker
                                        required property string message
                                        width: messagesColumn.width
                                        height: messageCard.implicitHeight

                                        Rectangle {
                                            id: messageCard
                                            property bool userMessage: speaker === "user"
                                            width: Math.min(parent.width * 0.88,
                                                Math.max(150, parent.width * (userMessage ? 0.60 : 0.84)))
                                            implicitHeight: messageContent.implicitHeight + 18
                                            anchors.left: userMessage ? undefined : parent.left
                                            anchors.right: userMessage ? parent.right : undefined
                                            radius: 5
                                            color: userMessage ? "#182735" : "#101b24"
                                            border.width: 1
                                            border.color: userMessage ? "#365468" : (speaker === "assistant" ? "#816b3e" : "#366676")

                                            Column {
                                                id: messageContent
                                                anchors.fill: parent
                                                anchors.margins: 9
                                                spacing: 0
                                                Text {
                                                    width: messageContent.width
                                                    text: message
                                                    wrapMode: Text.Wrap
                                                    color: speaker === "user" ? "#e8edf1" : "#e2e8e9"
                                                    font.family: "DejaVu Sans"
                                                    font.pixelSize: 13
                                                    lineHeight: 1.15
                                                    lineHeightMode: Text.ProportionalHeight
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }

                    Row {
                        width: parent.width
                        height: 34
                        spacing: 8
                        Rectangle {
                            id: microphoneButton
                            // Mute toggle: voice mode listens for the wake phrase, text mode is keyboard-only.
                            readonly property bool micOn: String(shell.ui.input_mode || "voice") === "voice"
                            width: 34
                            height: 34
                            radius: 3
                            color: micMouse.containsMouse ? "#262218" : "#0a121b"
                            border.width: 1
                            border.color: micOn
                                ? (micMouse.containsMouse ? "#e4bd64" : "#80672f")
                                : (micMouse.containsMouse ? "#e08a8a" : "#8a4a4a")
                            Rectangle {
                                width: 9
                                height: 15
                                radius: 5
                                anchors.horizontalCenter: parent.horizontalCenter
                                y: 4
                                color: "transparent"
                                border.width: 1.5
                                border.color: microphoneButton.micOn ? "#d7b65e" : "#b06a6a"
                            }
                            Rectangle {
                                width: 2
                                height: 6
                                anchors.horizontalCenter: parent.horizontalCenter
                                y: 19
                                color: microphoneButton.micOn ? "#d7b65e" : "#b06a6a"
                            }
                            Rectangle {
                                width: 13
                                height: 2
                                anchors.horizontalCenter: parent.horizontalCenter
                                y: 26
                                color: microphoneButton.micOn ? "#d7b65e" : "#b06a6a"
                            }
                            Rectangle {
                                visible: !microphoneButton.micOn
                                width: 22
                                height: 2
                                anchors.centerIn: parent
                                rotation: -45
                                color: "#e07a7a"
                            }
                            MouseArea {
                                id: micMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                onClicked: shell.sendAction("toggle_input_mode")
                            }
                        }
                        TextField {
                            id: inputField
                            width: parent.width - sendButton.width - microphoneButton.width - 16
                            height: 34
                            enabled: shell.ui.input_enabled !== false
                            focus: shell.ui.visible && shell.ui.expanded && enabled
                            placeholderText: "Напиши что-нибудь…"
                            color: "#eefcff"
                            placeholderTextColor: "#7095a2"
                            font.family: "DejaVu Sans"
                            font.pixelSize: 13
                            leftPadding: 10
                            rightPadding: 10
                            background: Rectangle {
                                radius: 3
                                color: "#e8040b13"
                                border.width: inputField.activeFocus ? 1.5 : 1
                                border.color: inputField.activeFocus ? "#d9b65c" : "#294957"
                            }
                            Keys.onPressed: function(event) {
                                if (event.key === Qt.Key_Escape) {
                                    shell.dismissAnimated()
                                    event.accepted = true
                                }
                            }
                            onAccepted: {
                                var value = text.trim()
                                if (value.length) {
                                    shell.sendAction("send_text", value)
                                    text = ""
                                }
                            }
                        }
                        Rectangle {
                            id: sendButton
                            width: 34
                            height: 34
                            radius: 3
                            color: sendMouse.containsMouse ? "#272217" : "#0a121b"
                            border.width: 1
                            border.color: sendMouse.containsMouse ? "#ffe08a" : "#a28641"
                            Text {
                                anchors.centerIn: parent
                                text: "➤"
                                color: "#e3c36d"
                                font.family: "DejaVu Sans"
                                font.pixelSize: 17
                                font.bold: true
                            }
                            MouseArea {
                                id: sendMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                onClicked: {
                                    var value = inputField.text.trim()
                                    if (value.length) {
                                        shell.sendAction("send_text", value)
                                        inputField.text = ""
                                    }
                                }
                            }
                        }
                    }
                }
            }

                }

                Item {
                    id: eyeStack
                    z: 3
                    x: 0
                    width: 140
                    height: 204
                    y: Math.max(0, hudGroup.height - height - 2)
                    opacity: shell.eyeProgress

                    // Tinted glass plate; Hyprland applies layer blur only where this
                    // translucent surface is visible (fully transparent pixels are ignored).
                    Rectangle {
                        id: companionGlass
                        x: 8
                        y: 8
                        width: parent.width - 16
                        height: parent.height - 41
                        radius: 4
                        color: "#6b3425"
                        opacity: 0.40
                        border.width: 1
                        border.color: "#5a9da0"
                    }

                    Canvas {
                        id: companionFrame
                        anchors.fill: parent
                        z: -2
                        onPaint: {
                            var ctx = getContext("2d")
                            ctx.clearRect(0, 0, width, height)
                            ctx.beginPath()
                            ctx.moveTo(9, 1)
                            ctx.lineTo(width - 18, 1)
                            ctx.lineTo(width - 1, 18)
                            ctx.lineTo(width - 1, height - 10)
                            ctx.lineTo(width - 11, height - 1)
                            ctx.lineTo(10, height - 1)
                            ctx.lineTo(1, height - 10)
                            ctx.lineTo(1, 10)
                            ctx.closePath()
                            // Keep only the Sheikah frame lines: the companion sprite
                            // must stay transparent against the actual desktop.
                            ctx.lineWidth = 1.25
                            ctx.strokeStyle = shell.ui.mode === "THINKING" || shell.ui.mode === "VISION" ? "#d7b55e"
                                            : shell.ui.mode === "ERROR" ? "#ed7a82" : "#a88b4b"
                            ctx.stroke()
                            ctx.beginPath()
                            ctx.moveTo(10, 7)
                            ctx.lineTo(width * 0.42, 7)
                            ctx.strokeStyle = "#56dbe7"
                            ctx.lineWidth = 1.2
                            ctx.stroke()
                            ctx.beginPath()
                            ctx.moveTo(8, height - 31)
                            ctx.lineTo(width - 8, height - 31)
                            ctx.strokeStyle = "rgba(85, 203, 217, 0.62)"
                            ctx.lineWidth = 1
                            ctx.stroke()
                            // Short inner rails make the companion plate read as a solid Sheikah device.
                            ctx.beginPath()
                            ctx.moveTo(8, 18)
                            ctx.lineTo(8, height - 40)
                            ctx.lineTo(15, height - 40)
                            ctx.moveTo(width - 8, 20)
                            ctx.lineTo(width - 8, height - 40)
                            ctx.lineTo(width - 15, height - 40)
                            ctx.strokeStyle = "rgba(83, 201, 213, 0.26)"
                            ctx.lineWidth = 1
                            ctx.stroke()
                            ctx.beginPath()
                            ctx.moveTo(width - 18, 1)
                            ctx.lineTo(width - 1, 18)
                            ctx.strokeStyle = "#56dbe7"
                            ctx.lineWidth = 1.1
                            ctx.stroke()
                        }
                    }

                    // Entire frame (not just the cutout sprite) accepts the click.
                    MouseArea {
                        anchors.fill: parent
                        z: -1
                        onClicked: {
                            if (shell.ui.mode === "LISTENING" || shell.ui.mode === "TRANSCRIBING") shell.sendAction("cancel")
                            else if (String(shell.ui.input_mode || "voice") === "text") shell.sendAction("expand")
                            else shell.sendAction("listen")
                        }
                    }

                    // Small, complete full-body sprite. Short clipped tears below restore the digital-artifact effect.
                    Image {
                        id: eyeImage
                        width: 100
                        height: 150
                        x: (parent.width - width) / 2 + shell.glitchStrength * shell.glitchOffset * 0.22
                        y: 18
                        source: shell.purahSpriteForMode(shell.ui.mode)
                        fillMode: Image.PreserveAspectFit
                        smooth: true
                        cache: true
                        opacity: (shell.ui.mode === "LISTENING" || shell.ui.mode === "TRANSCRIBING" || shell.ui.mode === "THINKING" || shell.ui.mode === "VISION")
                                ? (0.88 + 0.12 * shell.pulseLevel) : 1.0
                        scale: 1.0 + shell.glitchStrength * 0.012
                        Behavior on opacity { NumberAnimation { duration: 100 } }
                        Behavior on scale { NumberAnimation { duration: 90; easing.type: Easing.OutCubic } }
                    }
                    // Red/cyan RGB-like tears are clipped to narrow horizontal bands.
                    // They exist only during a glitch pulse, so they never create a permanent blurred shadow.
                    Item {
                        x: eyeImage.x + shell.glitchOffset
                        y: eyeImage.y + shell.glitchSliceOneY
                        width: eyeImage.width
                        height: shell.glitchSliceHeight
                        clip: true
                        visible: shell.glitchStrength > 0.12
                        opacity: shell.glitchStrength * 0.88
                        Image {
                            source: shell.purahSpriteForMode(shell.ui.mode)
                            width: eyeImage.width
                            height: eyeImage.height
                            y: -shell.glitchSliceOneY
                            x: -shell.glitchOffset * 1.6
                            smooth: true
                        }
                        Rectangle {
                            anchors.fill: parent
                            color: "#ff294f"
                            opacity: 0.18 * shell.glitchStrength
                        }
                    }
                    Item {
                        x: eyeImage.x - shell.glitchOffset * 0.70
                        y: eyeImage.y + shell.glitchSliceTwoY
                        width: eyeImage.width
                        height: Math.max(4, shell.glitchSliceHeight * 0.72)
                        clip: true
                        visible: shell.glitchStrength > 0.12
                        opacity: shell.glitchStrength * 0.90
                        Image {
                            source: shell.purahSpriteForMode(shell.ui.mode)
                            width: eyeImage.width
                            height: eyeImage.height
                            y: -shell.glitchSliceTwoY
                            x: shell.glitchOffset * 1.25
                            smooth: true
                        }
                        Rectangle {
                            anchors.fill: parent
                            color: "#27e8ff"
                            opacity: 0.17 * shell.glitchStrength
                        }
                    }
                    Rectangle {
                        x: eyeImage.x - 7 * shell.glitchStrength
                        y: eyeImage.y + shell.glitchSliceOneY + shell.glitchSliceHeight + 2
                        width: 34 + 40 * shell.glitchStrength
                        height: 1 + shell.glitchStrength
                        color: "#38eaff"
                        opacity: shell.glitchStrength * 0.85
                        visible: shell.glitchStrength > 0.18
                    }
                    Rectangle {
                        x: eyeImage.x + eyeImage.width - 26 + 7 * shell.glitchStrength
                        y: eyeImage.y + shell.glitchSliceTwoY - 3
                        width: 30 + 22 * shell.glitchStrength
                        height: 1.4
                        color: "#ff3158"
                        opacity: shell.glitchStrength * 0.84
                        visible: shell.glitchStrength > 0.18
                    }
                    Rectangle {
                        id: statusPlate
                        x: 7
                        y: 171
                        width: parent.width - 14
                        height: 25
                        color: "#0b1724"
                        border.width: 1
                        border.color: shell.ui.mode === "THINKING" || shell.ui.mode === "VISION" ? "#9a8042"
                                    : shell.ui.mode === "ERROR" ? "#b95861" : "#286d80"
                        Text {
                            anchors.fill: parent
                            anchors.leftMargin: 3
                            anchors.rightMargin: 3
                            verticalAlignment: Text.AlignVCenter
                            text: shell.statusLabelForMode(shell.ui.mode)
                            color: shell.ui.mode === "THINKING" || shell.ui.mode === "VISION" ? "#e6c76f"
                                 : shell.ui.mode === "SPEAKING" || shell.ui.mode === "TYPING" ? "#9ce4ba"
                                 : shell.ui.mode === "ERROR" ? "#ff9696" : "#87dce5"
                            font.family: "DejaVu Sans Mono"
                            font.pixelSize: 9
                            font.bold: true
                            font.letterSpacing: 0.35
                            horizontalAlignment: Text.AlignHCenter
                            elide: Text.ElideRight
                        }
                    }
                }
            }
        }
    }

    IpcHandler {
        target: "purah"
        function toggle(): void {
            if (shell.ui.visible) shell.sendAction("dismiss")
            else if (String(shell.ui.input_mode || "voice") === "text") shell.sendAction("expand")
            else shell.sendAction("listen")
        }
        function open(): void {
            if (String(shell.ui.input_mode || "voice") === "text") shell.sendAction("expand")
            else shell.sendAction("listen")
        }
        function close(): void { shell.sendAction("dismiss") }
        function dialog(): void { shell.sendAction("expand") }
    }
}
