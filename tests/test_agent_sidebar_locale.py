"""Real sidebar components resolve every supported locale without changing API values."""
import json
from pathlib import Path
import pytest
import gradio as gr
from modules.agent_ui import AgentPanel, REASONING_CHOICES, i18n

LOCALES = sorted(path.stem for path in Path('locale').glob('*.json'))

@pytest.mark.parametrize('language', LOCALES)
def test_sidebar_translations_and_internal_values(language):
    previous = i18n.language
    try:
        i18n.change_language(language)
        translations = json.loads(Path('locale', language + '.json').read_text())['ui']['toolbox']['agent']
        assert len(translations) == 32 and all(translations.values())
        assert all(i18n('ui.toolbox.agent.' + key) == value for key, value in translations.items())
        with gr.Blocks(analytics_enabled=False) as app:
            panel = AgentPanel()
            panel.selectors()
            panel.settings_components()
        assert panel.model.label == translations['model']
        assert panel.reasoning.label == translations['reasoning']
        assert panel.settings_group.label == translations['tools_title']
        for name in ('network', 'code', 'search', 'browser', 'screenshots', 'mcp'):
            assert getattr(panel, name).label == translations[name]
        assert [value for label, value in panel.reasoning.choices] == REASONING_CHOICES
        assert [value for label, value in panel.search_mode.choices] == ['live', 'cached', 'disabled']
        assert panel.reasoning.value == 'default' and panel.search_mode.value == 'live'
        assert panel.model.value == 'gpt-6-astra'
        assert panel.discovery.visible is False and panel.programmatic.visible is False
        app.close()
    finally:
        i18n.change_language(previous)
