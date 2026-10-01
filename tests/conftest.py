"""Use real Gradio; isolate app configuration/model imports to avoid API calls."""
import sys
import os
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
for name in ('ALL_PROXY', 'all_proxy', 'HTTP_PROXY', 'http_proxy', 'HTTPS_PROXY', 'https_proxy'):
    os.environ.pop(name, None)
os.environ['GRADIO_ANALYTICS_ENABLED'] = 'False'
os.environ['MPLCONFIGDIR'] = '/tmp/chuanhu-test-matplotlib'
sys.path.insert(0, str(ROOT))
shared = ModuleType('modules.shared')
shared.chuanhu_path = str(ROOT)
sys.modules['modules.shared'] = shared
presets = ModuleType('modules.presets')
presets.i18n = lambda text: text
sys.modules['modules.presets'] = presets
