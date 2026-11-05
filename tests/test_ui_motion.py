from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from app.ui.api_client import APIClient


def test_session_motion_and_native_navigation():
    app = AppTest.from_file('app/ui/streamlit_app.py', default_timeout=20)
    with patch.object(APIClient, 'get', return_value=[]), patch.object(APIClient, 'post') as writes:
        app.run()
        assert not app.exception
        assert any('class="rq-brand ms-brand-enter"' in m.value for m in app.markdown)
        app.run()
        assert not any('class="rq-brand ms-brand-enter"' in m.value for m in app.markdown)
        for page in ['Products', 'What-if Scenarios', 'Model Performance', 'Data & Settings', 'Overview']:
            # Avoid data contract downloads in the empty setup page.
            app.session_state['setup_step'] = 'Review validation'
            app.radio(key='page').set_value(page).run()
            assert not app.exception
            assert app.title[0].value == page
            assert not any('class="rq-brand ms-brand-enter"' in m.value for m in app.markdown)
        writes.assert_not_called()


def test_summary_entrance_only_once_and_values_update_immediately():
    app = AppTest.from_string('''
import streamlit as st
from app.ui.common import theme, header, metric
with st.container(key='ms-workspace'):
    theme()
    header('Overview', 'Saved decisions')
    value = st.number_input('Value', value=10)
    metric(st.columns(1)[0], 'Units', value, 'Saved value')
''').run()
    assert not app.exception
    assert any('animation:ms-card-in' in m.value for m in app.markdown if m.value.startswith('<style>.st-key-ms-card'))
    app.number_input[0].set_value(25).run()
    assert not app.exception
    assert app.metric[0].value == '25'
    assert any('animation:none' in m.value for m in app.markdown if m.value.startswith('<style>.st-key-ms-card'))
    css = Path('app/ui/styles.css').read_text(encoding='utf-8')
    assert 'prefers-reduced-motion:reduce' in css
    assert 'animation:none!important' in css
    assert 'https://' not in css and 'url(' not in css
