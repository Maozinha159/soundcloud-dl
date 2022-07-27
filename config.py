import os
import json
import utils

_default_configs_paths = [
    r"%USERPROFILE%\scdl.conf",
    r"%USERPROFILE%\scdl\config.json",
    r"%APPDATA%\scdl\config.json",
    rf"{utils.CODE_DIR}\config.json"
] if utils.WINDOWS else [
    "${HOME}/.scdl.conf",
    "${XDG_CONFIG_HOME}/scdl.conf"
    if os.environ.get("XDG_CONFIG_HOME") else
    "${HOME}/.config/scdl.conf",
    "/etc/scdl.conf",
    f"{utils.CODE_DIR}/config.json"
]

for i in range(len(_default_configs_paths)):
    _default_configs_paths[i] = os.path.expandvars(_default_configs_paths[i])

_default_config = {
    "directory"        : ".",
    "oauth_token"      : None,
    "prefer_opus"      : False,
    "low_quality"      : False,
    "download_original": True,
    "process_original" : True,
    "compression_level": 12
}

def get_config() -> dict:
    for path in _default_configs_paths:
        if os.path.isfile(path):
            with open(path) as f:
                config = json.load(f)
            UwU = _default_config.copy()
            UwU.update(config)
            return UwU
    return _default_config.copy()
