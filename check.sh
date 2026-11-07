#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-python3}"
"$PYTHON" -m py_compile main.py ensure_config.py provider_config.py configure_tts.py tts_engine_utils.py tts_text.py vision.py kokoro_ru_tts.py test_tts_text.py test_vision.py test_kokoro_ru_tts.py test_kokoro_installer_manifest.py test_tts_playback.py test_wakeword_logic.py test_tts_engine_utils.py test_configure_tts.py test_tts_runtime.py test_quickshell_hud.py test_config_priority.py test_auth_key_isolation.py test_api_routing.py test_full_access_tools.py test_deepseek_reasoning_persistence.py test_ensure_config_migration.py test_purah_terminal.py test_telegram_connector.py test_desktop_backends.py test_browser_firefox.py test_tool_specs.py test_release_integrity.py test_install_readiness.py doctor.py purah_terminal.py purah_agent/telegram.py purah_agent/ui.py purah_agent/model_worker.py purah_agent/tts_worker.py purah_agent/tool_journal.py purah_agent/accessibility.py purah_agent/browser_cdp.py purah_agent/browser_common.py purah_agent/browser_bidi.py purah_agent/browser.py purah_agent/tool_specs.py purah_agent/editor.py purah_agent/file_manager.py setup_telegram.py sudo_askpass.py
for script in install.sh run-purah.sh start-purah.sh setup_voice.sh setup_piper_tts.sh setup_kokoro_ru.sh diagnose-tts.sh check.sh; do
  bash -n "$script"
done
"$PYTHON" - <<'PY'
import ast
import re
import struct
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

config_text = Path('config.toml.example').read_text(encoding='utf-8')
config = tomllib.loads(config_text)
setup = Path('setup_kokoro_ru.sh').read_text(encoding='utf-8')
assert 'PURAH Kokoro installer 1.1.3' in setup, 'Kokoro installer version banner missing'
assert 'APP_VERSION = "1.4.2"' in Path('main.py').read_text(encoding='utf-8'), 'application version not bumped to 1.4.2'
assert 'def valid_g2p_config' in setup and 'kokoro-config.json?download=true' in setup, 'explicit G2P config recovery missing'
assert 'snapshot_download' in setup, 'Kokoro installer must fetch the repo espeak-data subtree'
assert 'allow_patterns=["espeak-data/*", "espeak-data/**"]' in setup, 'eSpeak-data download must be restricted to its folder'
assert 'file_count < 100' in setup and 'total_bytes < 5_000_000' in setup, 'eSpeak data sanity checks missing'
blocks = re.findall(r"<<'PY'\n(.*?)\nPY", setup, flags=re.S)
assert len(blocks) == 3, f'expected three Python heredocs in setup_kokoro_ru.sh, got {len(blocks)}'
for index, block in enumerate(blocks, start=1):
    ast.parse(block, filename=f'setup_kokoro_ru.sh heredoc {index}')
assert {'api', 'speech', 'terminal', 'assistant', 'desktop', 'wakeword', 'tts', 'vision', 'browser', 'editor', 'memory'} <= config.keys()
assert config['api']['model'] == 'deepseek-flash' and config['api']['enable_tools'] is True, 'DeepSeek full-agent defaults missing'
assert config['api']['thinking_mode'] in {'enabled', 'disabled'}, 'DeepSeek thinking mode config missing'
assert config['vision']['inherit_api'] is False and config['vision']['model'] == 'qwen/qwen3.8-27b' and config['vision']['chat_url'] == 'https://api.groq.com/openai/v1/chat/completions', 'vision must be configured independently from chat by default'
assert config['wakeword']['phrase'] == 'Привет, Пура'
prompt_file = Path(config['assistant']['system_prompt_file'])
assert prompt_file.is_file() and 'Пура' in prompt_file.read_text(encoding='utf-8') and len(prompt_file.read_text(encoding='utf-8')) > 20
assert 'system_prompt' in config['vision'] and len(config['vision']['system_prompt']) > 20
main_source = Path('main.py').read_text(encoding='utf-8')
tool_specs_source = Path('purah_agent/tool_specs.py').read_text(encoding='utf-8')
ui_source = Path('purah_agent/ui.py').read_text(encoding='utf-8')
tts_source = Path('purah_agent/tts_worker.py').read_text(encoding='utf-8')
assert 'if window.layer_overlay:' in main_source and 'window._overlay_set_visible(True, expanded=True)' in main_source, 'IPC show must not open Qt toplevel in overlay mode'
assert 'ОБЯЗАТЕЛЬНЫЙ ФОРМАТ VISION' in main_source, 'vision concise guardrail missing for migrated prompts'
assert 'ОБЯЗАТЕЛЬНЫЙ СТИЛЬ ОТВЕТА' in main_source, 'chat concise guardrail missing'
assert config['tts']['engine'] in {'edge', 'piper', 'espeak_ng', 'kokoro_ru', 'none'}
assert config['tts']['chunk_chars'] >= 80
assert 'warmup_on_start' in config['tts'] and config['tts']['warmup_on_start'] is False and 'kokoro_threads' in config['tts']
assert config['tts']['espeak_voice'] == 'ru'
ET.parse('assets/sheikah_crest.svg')
# Sprite assets are normalized transparent canvases so switching poses never crops or jumps.
for sprite in ('purah_listening.png', 'purah_thinking.png', 'purah_answering.png'):
    for location in (Path('assets') / sprite, Path('quickshell/purah') / sprite):
        data = location.read_bytes()
        assert data[:8] == b'\x89PNG\r\n\x1a\n', f'invalid sprite PNG: {location}'
        width, height = struct.unpack('>II', data[16:24])
        assert (width, height) == (240, 360), f'{location} is not a full-frame 240x360 sprite canvas'
# Newly sliced animation frames must be present identically in the asset root and Quickshell's fallback directory.
animated_sprites = [
    'purah_phoneme_neutral.png', 'purah_phoneme_a_ya.png', 'purah_phoneme_o_yo.png',
    'purah_phoneme_u_yu.png', 'purah_phoneme_i_y.png', 'purah_phoneme_e_ye.png',
    'purah_phoneme_m_b_p.png', 'purah_phoneme_f_v.png', 'purah_phoneme_t_d_n.png',
    'purah_phoneme_k_g_h.png', 'purah_phoneme_s_z.png', 'purah_phoneme_sh_zh_shch_ch.png',
    'purah_phoneme_l_r.png', 'purah_phoneme_y.png', 'purah_phoneme_transition.png',
    'purah_phoneme_smile.png', 'purah_blink_open.png', 'purah_blink_half_start.png',
    'purah_blink_closed.png', 'purah_blink_half_end.png', 'purah_blink_smile_closed.png',
    'purah_blink_squint.png',
]
for sprite in animated_sprites:
    root_asset = Path('assets') / sprite
    qs_asset = Path('quickshell/purah') / sprite
    assert root_asset.is_file() and qs_asset.is_file(), f'missing animated sprite copy: {sprite}'
    assert root_asset.read_bytes() == qs_asset.read_bytes(), f'asset copies differ: {sprite}'
    data = root_asset.read_bytes()
    assert data[:8] == b'\x89PNG\r\n\x1a\n', f'invalid animated sprite PNG: {root_asset}'
    width, height = struct.unpack('>II', data[16:24])
    assert (width, height) == (240, 360), f'{root_asset} is not a normalized 240x360 frame'
qml = Path('quickshell/purah/shell.qml').read_text(encoding='utf-8')
assert 'WlrLayershell.namespace: "purah-overlay"' in qml, 'Quickshell layer namespace missing'
assert 'Component.onCompleted: readyFile.setText("ready\\n")' in qml, 'QML ready handshake must reference FileView id directly'
assert 'shell.readyFile.setText' not in qml, 'invalid shell.readyFile reference would break ready handshake'
assert 'file://' in qml and 'return "file://" + appDir + "/assets/" + name' in qml, 'eye SVGs must use physical filesystem paths, not blackholed relative qs URLs'
assert 'Qt.resolvedUrl("../../assets/sheikah_crest.svg")' not in qml, 'relative URL escaping the Quickshell shellDir is blackholed'
assert 'WlrLayershell.layer: WlrLayer.Overlay' in qml, 'overlay layer missing'
assert 'ExclusionMode.Ignore' in qml, 'overlay must not reserve a screen edge'
assert 'mask: Region { item: interactionRegion }' in qml, 'full-screen overlay must pass clicks through outside the HUD'
assert 'WlrLayershell.keyboardFocus: shell.ui.visible ? WlrKeyboardFocus.OnDemand : WlrKeyboardFocus.None' in qml and 'WlrKeyboardFocus.Exclusive' not in qml, 'overlay must not lock keyboard focus away from other apps'
assert 'id: interactionRegion' in qml and 'onClicked: shell.sendAction("dismiss")' not in qml[:qml.index('id: bubble')], 'outside clicks must not dismiss Purah'
assert 'id: hudGroup' in qml and 'shell.hudLayoutProgress' in qml, 'character and dialogue must share an animated layout group'
assert 'id: bubbleViewport' in qml and 'clip: true' in qml, 'dialogue must reveal through a clipped CRT column wipe'
assert 'dialogTotalColumns: 64' in qml and 'dialogRevealColumns' in qml and 'dialogOpenAnimation' in qml, 'CRT bar-by-bar opening animation is missing'
assert 'Math.max(hudGroup.compactWidth, bubbleViewport.x + bubbleViewport.width)' in qml, 'input mask should expand only as dialog columns are revealed'
assert 'function transcriptEntries(value)' in qml and 'messageCard' in qml, 'transcript should render user/assistant messages as distinct Sheikah bubbles'
assert 'function syncTranscript(value)' in qml and 'if (source === lastTranscriptText) return' in qml, 'heartbeat polling must not rebuild chat on every state update'
assert 'id: eyeStack\n                    z: 3\n                    x: 0' in qml, 'expanded character must dock at the left of the dialogue instead of remaining centered'
assert 'shell.purahSpriteForMode(shell.ui.mode)' in qml and 'eyeSourceForMode' not in qml, 'character sprite must replace the old eye SVG'
assert 'purah_blink_open.png' in qml and 'purah_phoneme_neutral.png' in qml, 'idle blink and speaking phoneme sprites must be wired into QML'
assert 'function phonemeFrameForCharacter(character)' in qml and 'function updateSpeechFrameSequence()' in qml, 'text-paced phoneme mapping is missing'
assert 'purah_blink_half_start.png' in qml and 'purah_blink_closed.png' in qml and 'purah_blink_half_end.png' in qml, 'complete eye blink sequence is missing'
assert 'if not recognizer.AcceptWaveform(samples.tobytes()):' in main_source and 'contains_wake_phrase(text, grammar_phrases)' in main_source, 'wakeword must only match finalized audio, not partial hypotheses'
assert 'never accept a bare name' in Path('wakeword_logic.py').read_text(encoding='utf-8'), 'wakeword grammar should be strict'
assert 'glitchStrength' in qml and 'Red/cyan RGB-like tears are clipped' in qml, 'digital artifact glitch tears missing'
assert 'glitchSliceOneY' in qml and 'glitchSliceTwoY' in qml, 'randomized glitch bands missing'
assert 'glitchEchoRed' not in qml and 'glitchEchoCyan' not in qml, 'sprite ghost layers would blur the character'
assert 'waveformPhase' not in qml and 'waveformTimer' not in qml, 'fake audio visualizer must be removed'
assert 'statusLabelForMode' in qml and 'text: shell.ui.mode || "CONNECTED"' not in qml, 'status should be shown only as a concise label under Purah'
assert 'expandedTargetWidth: Math.min(390' in qml and 'readonly property real dialogHeight: 238' in qml, 'expanded HUD should remain compact and docked'
assert 'id: companionFrame' in qml and 'id: statusPlate' in qml and 'ctx.fillStyle = "#050d17"' not in qml and 'color: "#0b1724"' in qml, 'Purah frame must be transparent while the status and chat text backgrounds remain'
assert 'property real dialogScanAlpha' in qml and 'shell.dismissAnimated()' in qml, 'dialog CRT scanline and close animation are required'
assert 'readonly property real dialogHeight: 238' in qml and 'x: 24' in qml and 'y: contentRoot.height - compactHeight - 18' in qml, 'HUD should be bottom-left with compact dialogue above Purah'
for asset in ('sheikah_crest_listening.svg','sheikah_crest_transcribing.svg','sheikah_crest_thinking.svg','sheikah_crest_speaking.svg','sheikah_crest_error.svg','sheikah_crest_glitch_red.svg','sheikah_crest_glitch_cyan.svg'):
    ET.parse('assets/' + asset)
    assert Path('quickshell/purah/' + asset).is_file(), f'Quickshell fallback asset missing: {asset}'
assert 'sequence: "Escape"' in qml and 'shell.sendAction("dismiss")' in qml, 'Escape must dismiss both eye and chat'
assert 'quickshell-ready' in qml and 'responseFlash' in qml, 'overlay startup handshake/green completion flash missing'
assert 'Date.now() - incomingHeartbeat > 6500' in qml, 'stale overlay state guard missing'
assert 'self._overlay_set_visible(False, expanded=False)' not in ui_source[ui_source.index('def on_vision_capture_requested'):ui_source.index('def handle_tool_approval')], 'Quickshell vision capture must not unmap the HUD and retrigger entrance animation'
assert 'игнорируй его и анализируй приложение за ним' in main_source, 'vision model must ignore its own Purah HUD'
lua_rules = Path('hyprland-purah-rule.lua').read_text(encoding='utf-8')
conf_rules = Path('hyprland-purah-rule.conf').read_text(encoding='utf-8')
assert 'layer_rule' in lua_rules and 'purah-overlay' in lua_rules
assert 'layerrule = blur on, ignore_alpha 0.08, match:namespace ^purah-overlay$' in conf_rules
assert 'windowrule' not in conf_rules.lower(), 'no Qt fallback windowrules should remain'
assert 'window_rule' not in lua_rules.lower(), 'no Qt fallback window rules should remain'
required = [
    'main.py', 'provider_config.py', 'test_api_routing.py', 'test_auth_key_isolation.py', 'tts_text.py', 'vision.py', 'kokoro_ru_tts.py',
    'test_tts_text.py', 'test_vision.py', 'test_kokoro_ru_tts.py',
    'test_kokoro_installer_manifest.py', 'test_tts_playback.py', 'test_wakeword_logic.py', 'test_quickshell_hud.py', 'test_desktop_agent.py', 'wakeword_logic.py',
    'configure_tts.py', 'tts_engine_utils.py', 'test_tts_engine_utils.py', 'test_configure_tts.py', 'test_tts_runtime.py', 'diagnose-tts.sh', 'test_config_priority.py', 'test_full_access_tools.py', 'test_deepseek_reasoning_persistence.py', 'test_ensure_config_migration.py', 'sudo_askpass.py',
    'requirements.txt', 'requirements-wakeword.txt', 'requirements-kokoro-ru.txt', 'purah_terminal.py', 'purah_agent/telegram.py', 'purah_agent/ui.py', 'purah_agent/model_worker.py', 'purah_agent/tts_worker.py', 'purah_agent/accessibility.py', 'purah_agent/browser_cdp.py', 'purah_agent/browser_common.py', 'purah_agent/browser_bidi.py', 'purah_agent/browser.py', 'purah_agent/tool_specs.py', 'purah_agent/editor.py', 'purah_agent/file_manager.py', 'purah_agent/tool_journal.py', 'requirements-desktop.txt', 'setup_telegram.py', 'requirements-telegram.txt', 'docs/telegram.md', 'docs/ARCHITECTURE_RU.md', 'docs/INTEGRATIONS_RU.md', 'docs/CHANGELOG_1_4_0_RU.md', 'docs/CHANGELOG_1_4_1_RU.md', 'docs/CHANGELOG_1_4_2_RU.md', 'test_purah_terminal.py', 'test_telegram_connector.py', 'test_desktop_backends.py', 'test_browser_firefox.py', 'test_tool_specs.py', 'test_release_integrity.py', 'test_install_readiness.py', 'doctor.py',
    'install.sh', 'hyprland-purah-rule.conf', 'hyprland-purah-rule.lua',
    'purah.env.example', 'quickshell/purah/shell.qml', 'quickshell/purah/sheikah_crest.svg', 'run-purah.sh', 'start-purah.sh',
    'assets/purah_listening.png', 'assets/purah_thinking.png', 'assets/purah_answering.png',
    'quickshell/purah/purah_listening.png', 'quickshell/purah/purah_thinking.png', 'quickshell/purah/purah_answering.png',
    'setup_voice.sh', 'setup_piper_tts.sh', 'setup_kokoro_ru.sh', 'ensure_config.py',
]
missing = [p for p in required if not Path(p).is_file()]
assert not missing, f'missing project files: {missing}'
assert 'source \"$ROOT/.venv/bin/activate\"' not in Path('start-purah.sh').read_text(encoding='utf-8'), 'startup must not require manual venv activation'
assert 'hyprctl keyword layerrule \"blur on, ignore_alpha 0.08, match:namespace ^purah-overlay$\"' in Path('start-purah.sh').read_text(encoding='utf-8'), 'launcher must set layer blur rule live'
assert 'RULE_MARKER=' in Path('start-purah.sh').read_text(encoding='utf-8'), 'layer rules must not be appended on every voice bind'
assert 'stop_project_quickshells' in Path('start-purah.sh').read_text(encoding='utf-8'), 'stale shell cleanup must prevent repeated zombie Quickshell sessions'
assert 'current start attempt only' in Path('start-purah.sh').read_text(encoding='utf-8'), 'startup log should not preserve stale errors as current errors'
main_source = Path('main.py').read_text(encoding='utf-8')
tool_specs_source = Path('purah_agent/tool_specs.py').read_text(encoding='utf-8')
assert 'CONFIG_AUTHORITATIVE_ENV' in main_source and 'config.toml/defaults override inherited' in main_source, 'config.toml must override inherited provider/model values'
assert 'resolve_vision_route' in main_source and 'same_provider_origin' in main_source and 'PURAH_VISION_INHERIT_API' in main_source, 'vision provider route must be independent unless inheritance is explicitly enabled'
assert '_auth_headers(url, os.getenv("PURAH_STT_API_KEY", ""), key_setting="[speech].stt_api_key")' in main_source, 'STT must not silently reuse the text API key'
assert 'allow_tools=allow_tools' in main_source and 'allow_tools = False' in main_source, 'vision requests must not forward chat-agent tools to an unrelated vision provider'
assert 'PURAH_TTS_ENV_OVERRIDE' not in main_source, 'no environment option may override an explicit config value'
assert 'terminal.full_access' in tool_specs_source and 'PURAH_TERMINAL_FULL_ACCESS' in main_source and 'sudo -A COMMAND' in tool_specs_source, 'full user-level system access and sudo askpass must be wired'
assert 'env.pop("SUDO_ASKPASS", None)' in Path('purah_terminal.py').read_text(encoding='utf-8'), 'terminal.allow_sudo must override an inherited askpass helper'
assert 'reasoning_content' in main_source and 'PURAH_THINKING_MODE' in main_source, 'DeepSeek thinking-mode tool calls must preserve reasoning_content'
assert 'message.pop("reasoning_content", None)' in main_source and 'reasoning_content TEXT NOT NULL DEFAULT' in main_source, 'DeepSeek reasoning state must round-trip safely in hidden SQLite metadata'
assert 'payload_obj["thinking"] = {"type": "disabled"}' not in main_source, 'thinking_mode from config must not be overwritten later'
assert '"PUT_YOUR_DEEPSEEK_API_KEY_HERE"' in main_source, 'DeepSeek key placeholder must be rejected as unconfigured'
assert 'PURAH CONFIG ERROR' in main_source and 'config.toml/defaults override inherited' in main_source, 'invalid/explicit config should fail closed rather than fall back to an old provider'
assert 'else "[vision].api_key"' in main_source and 'key_setting="[speech].stt_api_key"' in main_source and 'same_provider_origin' in main_source, 'API keys must be isolated by provider'
assert 'def print_effective_config()' in main_source and '--print-effective-config' in main_source, 'safe effective-config diagnostic command missing'
assert Path('sudo_askpass.py').is_file(), 'local GUI sudo askpass helper missing'
assert 'visible_terminal_restore_focus' in config['terminal'] and config['terminal']['visible_terminal_restore_focus'] is True
assert config['browser']['automation_backend'] in {'auto', 'firefox', 'chromium'}
assert config['browser']['firefox_bidi_enabled'] is False and config['browser']['firefox_bidi_port'] == 9223
assert 'browser_automation_enabled()' in main_source
assert 'websocket-client' in Path('requirements-desktop.txt').read_text() and 'requirements-desktop.txt' in Path('install.sh').read_text()
assert 'browser_navigate_tab' in main_source and 'browser_read_tab' in main_source
assert config['terminal']['full_access'] is True and config['terminal']['allow_sudo'] is True, 'full system access must be the default config'
assert 'def _interrupt_current_turn(' in ui_source and 'interrupt_mode=True' in ui_source, '1.1.1 barge-in logic was lost during TTS merge'
assert '_TTS_SYNTHESIS_LOCK' in tts_source, 'serialized TTS synthesis/cancellation guard was lost'
assert 'elif self.engine == "espeak_ng":' in tts_source and 'espeak_command(' in tts_source, 'eSpeak NG backend missing'
assert 'from kokoro_ru_tts import' not in '\n'.join(main_source.splitlines()[:300]), 'Kokoro runtime must remain lazily imported'
assert 'PURAH_KOKORO_WARMUP_ON_START", "false"' in main_source, 'Kokoro warmup must be opt-in by default'
print('PURAH checks passed: Python/shell syntax, TOML, TTS engine contracts, SVG, Quickshell overlay, Hyprland rules, project files')
PY
"$PYTHON" -m unittest test_tts_text.py test_vision.py test_kokoro_ru_tts.py test_kokoro_installer_manifest.py test_wakeword_logic.py test_quickshell_hud.py test_config_priority.py test_auth_key_isolation.py test_api_routing.py test_full_access_tools.py test_tts_runtime.py test_tts_engine_utils.py test_configure_tts.py test_deepseek_reasoning_persistence.py test_ensure_config_migration.py test_desktop_agent.py test_purah_terminal.py test_telegram_connector.py test_desktop_backends.py test_browser_firefox.py test_tool_specs.py test_release_integrity.py test_install_readiness.py
