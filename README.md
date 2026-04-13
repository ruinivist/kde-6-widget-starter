# KDE 6 Widget Starter

An all-included template for creating KDE 6 widgets.

## Features

- **Hot Reload**: `make watch` automatically restarts your widget when you modify files.
- **Standard Structure**: Follows KDE Plasma 6 development standards.
- **Integrated Build Tools**: Simple `Makefile` for all common tasks.
- **Testing Environments**: Easily test in panel, desktop, or HiDPI modes.
- **C++ Bridge Demo**: Includes a Qt6 C++ QML module that can be called directly from QML.
- **Upload to Pling**: Configured in a GitHub Action; add the needed secrets.

## Prerequisites

You need the basic KDE and Qt 6 development tools installed on your Linux distribution.
This also expects you to use clangd as your C++ language server for editor integration along with the clangd VS Code extension.

**Arch Linux:** ( approximate list, and you'll likely have these already on KDE )

```bash
sudo pacman -S plasma-sdk inotify-tools kirigami2 qt6-base qt6-declarative
```

## Quick Start

1. Clone this repository.
2. Run `make hooks-install` once to enable Git hooks from `.githooks/`.
3. If you use VS Code, run `make cpp-build` once to build the C++ bridge and generate IDE metadata for autocomplete. Then install the official QML extension and reload the VS Code window.
4. Start hot reload while editing with `make watch`.
5. For a local install into Plasma's package store, use `make install`.
6. Check style/lint status with `make lint`.
7. Format everything with `make format`.
8. Test with `make dev` (default mode) or `make dev MODE=panel|desktop|hidpi`.
9. Update `package/metadata.json` with your details
10. Package for distribution with `make package` (creates `build/<widget-id>.plasmoid`, default: `build/org.kde.plasma.starter.plasmoid`). This is just a zip file with a different extension, so you can inspect it with any archive manager.
11. Add GitHub secrets, tag a commit with a `release` prefix, and push to upload automatically to Pling.

## Editor Setup Notes

- **QML IntelliSense (VS Code):** Workspace settings pin `qmlls` to `/usr/lib/qt6/bin/qmlls` and pass `-I/usr/lib/qt6/qml`, `-b${workspaceFolder}/build/cpp`, and `-d/usr/share/doc/qt6`.
- **Important:** Run `make cpp-build` at least once so the `build/cpp` metadata used by `qmlls` exists.
- **Generated C++ QML module:** `package/contents/ui/cppbridge/` is created by the C++ build, so editors may warn about `import "cppbridge"` before first build.
- **Known qmllint false-positive:** `plasmoid.configuration.*` is runtime-injected by Plasma; `main.qml` has a targeted `qmllint` suppression for that property lookup.
- **C++ editor config:** `clangd` uses `build/cpp/compile_commands.json` (set in `.vscode/settings.json`), with project defaults in `.clangd` and `.clang-format`.

## Project Structure

```
.
├── cpp/                        # C++ source and CMake config for the QML bridge module
│   ├── CMakeLists.txt
│   └── src/
│       ├── timebridge.h
│       └── timebridge.cpp
├── package/                    # The source code of your widget
│   ├── metadata.json           # Widget metadata (name, ID, version)
│   └── contents/
│       ├── ui/                 # QML User Interface files
│       │   ├── main.qml        # Main widget UI (entry point)
│       │   ├── configGeneral.qml # UI for the "General" settings tab
│       │   └── cppbridge/      # Generated Qt6 QML module files (.so + qmldir), built by make cpp-build
│       └── config/             # Configuration schema
│           ├── config.qml      # Configuration loader (defines the tabs, more of a placeholder file)
│           └── main.xml        # Settings schema (defines variables & defaults)
├── scripts/
│   └── watch.sh                # Hot reload logic (used by make watch)
└── Makefile                    # Command runner
```

## KDE Plasma Quirks: Configuration Logic vs UI

KDE Plasma separates **Configuration Logic** (Backend) from **Configuration UI** (frontend). This is why there are two "config" related folders:

1.  **The Schema (`contents/config/main.xml`)**:
    - This is the **Database Definition**. It defines the variable names, data types (String, Bool), and **Default Values**.
    - It has _no UI logic_. It just defines what can be saved.

2.  **The View (`contents/ui/configGeneral.qml`)**:
    - This is the **User Interface**. It contains the text fields and checkboxes.
    - It binds to the variables defined in `main.xml`.

## Packaging Notes

Artifacts produced by `make package` are intended for KDE 6 on Linux x86_64.

## Pling Upload

This repo includes a script for OpenDesktop/Pling publishing as part of CI/CD actions.

```bash
uv run --env-file .env python scripts/pling_upload.py --dry-run
```

`--dry-run` validates login, project edit-page access, and upload endpoint discovery.

Live mode is the default when `--dry-run` is omitted:

```bash
uv run --env-file .env python scripts/pling_upload.py \
  -f build/org.kde.plasma.starter.plasmoid
```

Important: live mode is destructive by design right now. It first deletes all
existing files on the target project, then uploads/registers the provided file(s).

You can pass multiple files by repeating `-f`:

```bash
uv run --env-file .env python scripts/pling_upload.py \
  -f test2.md \
  -f test3.md
```

Config can be provided by args or environment variables (`.env.example`):
`PLING_PROJECT_ID`, `PLING_USERNAME`, `PLING_PASSWORD`,
`PLING_FILES`, `PLING_TIMEOUT`, `PLING_MAX_RETRIES`.

For example,

```env
PLING_FILES=build/org.kde.plasma.starter.plasmoid,README.md,CHANGELOG.md
```

## GitHub Actions Release Upload

This repository includes a release upload workflow at
`.github/workflows/pling-release-upload.yml`.

Behavior:

- Trigger: push tags matching `release*` (for example `release`, `release-v1.2.0`).
- Build: installs Qt/C++ dependencies on the runner and runs `make package`.
- Package output: `build/<widget-id>.plasmoid` (resolved from `package/metadata.json`).
- Upload: runs `scripts/pling_upload.py` with the packaged `.plasmoid` artifact.

Required GitHub repository secrets:

- `PLING_PROJECT_ID` : For `https://www.opendesktop.org/p/2355726/`, this'll be `2355726`
- `PLING_USERNAME` : Email, you need to use the email/password login method.
- `PLING_PASSWORD`

## License

MIT
