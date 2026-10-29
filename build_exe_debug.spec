# -*- mode: python ; coding: utf-8 -*-
#
# Собирает ТОНКУЮ обёртку launcher_bootstrap.py -- сам launcher.py
# и остальные .py файлы НЕ упаковываются внутрь EXE, а читаются
# с диска при каждом запуске. Поэтому редактирование launcher.py
# (или любого другого .py файла проекта) НЕ требует пересборки EXE.

block_cipher = None

a = Analysis(
    ['launcher_bootstrap.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=[
        'tkinter',
        'tkinter.ttk',
        'tkinter.filedialog',
        'tkinter.messagebox',
        'tkinter.scrolledtext',
        'subprocess',
        'threading',
        'signal',
        're',
        'datetime',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'PIL.ImageTk',
        'uuid',
        'json',
        'requests',
        'urllib3',
        'idna',
        'certifi',
        'charset_normalizer',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='VoiceAssistant_debug',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,           # DEBUG: показывать консоль с ошибками
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon.ico' if __import__('os').path.exists('icon.ico') else None,
)
