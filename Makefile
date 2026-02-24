# Makefile for KDE 6 Plasma Widget Development

# Variables
PACKAGE_DIR := package
METADATA_FILE := $(PACKAGE_DIR)/metadata.json
WIDGET_ID := $(shell grep -oP '"Id": "\K[^"]+' $(METADATA_FILE))
BUILD_DIR := build
PLASMOID_FILE := $(WIDGET_ID).plasmoid
CPP_DIR := cpp
CPP_BUILD_DIR := $(BUILD_DIR)/cpp
CPP_MODULE_DIR := $(PACKAGE_DIR)/contents/ui/cppbridge

# Default target
.PHONY: help
help:
	@echo "KDE Plasma 6 Widget Developer Tools"
	@echo ""
	@echo "Usage:"
	@echo "  make test          Run widget in plasmoidviewer (default)"
	@echo "  make test-local    Run widget directly from local package path"
	@echo "  make test-panel    Run in horizontal panel mode"
	@echo "  make test-desktop  Run as a floating desktop widget"
	@echo "  make test-hidpi    Run with 2x DPI scaling"
	@echo "  make watch         Start Development Mode with Hot Reload"
	@echo "  make cpp-build     Build the C++ QML bridge module"
	@echo "  make cpp-clean     Remove C++ bridge build artifacts"
	@echo "  make install       Install/upgrade widget (converts dev symlink)"
	@echo "  make uninstall     Remove widget from local system"
	@echo "  make package       Build .plasmoid file for distribution"
	@echo "  make clean         Remove build artifacts"
	@echo "  make format        Format QML, C++, JSON, YAML, and Markdown files"
	@echo "  make lint          Run non-mutating lint and formatting checks"
	@echo "  make hooks-install Configure Git to use versioned hooks from .githooks/"

# Git Hooks
# Configure Git to use versioned hooks stored in this repository.
.PHONY: hooks-install
hooks-install:
	git config core.hooksPath .githooks
	@echo "Configured core.hooksPath=.githooks"

# C++ Bridge Build Targets
# Generate CMake build files for the C++ QML bridge.
.PHONY: cpp-configure
cpp-configure:
	mkdir -p $(CPP_BUILD_DIR)
	cd $(CPP_DIR) && cmake --preset clang-default

# Build the C++ QML bridge module binaries.
.PHONY: cpp-build
cpp-build: cpp-configure
	cmake --build $(CPP_BUILD_DIR)

# Remove C++ bridge build output and copied module artifacts.
.PHONY: cpp-clean
cpp-clean:
	rm -rf $(CPP_BUILD_DIR) $(CPP_MODULE_DIR)

# Testing Targets
# Run plasmoidviewer using the installed widget ID from metadata.
.PHONY: test
test: cpp-build
	plasmoidviewer -a $(WIDGET_ID)

# Run plasmoidviewer directly from the local package directory.
.PHONY: test-local
test-local: cpp-build
	plasmoidviewer -a $(PACKAGE_DIR)

# Run widget in panel simulation (top edge, horizontal form factor).
.PHONY: test-panel
test-panel: cpp-build
	plasmoidviewer -a $(WIDGET_ID) -l topedge -f horizontal

# Run widget in desktop simulation (floating, planar form factor).
.PHONY: test-desktop
test-desktop: cpp-build
	plasmoidviewer -a $(WIDGET_ID) -l floating -f planar

# Run widget with HiDPI scaling enabled (2x scale factor).
.PHONY: test-hidpi
test-hidpi: cpp-build
	QT_SCALE_FACTOR=2 plasmoidviewer -a $(WIDGET_ID)

# Development Tools
# Start file watcher for iterative development and reload workflow.
.PHONY: watch
watch: cpp-build
	./scripts/watch.sh

# Format all QML files in the package directory in-place.
.PHONY: format-qml
format-qml:
	@command -v qmlformat >/dev/null 2>&1 || { echo "Missing required tool: qmlformat"; exit 1; }
	find $(PACKAGE_DIR) -name "*.qml" -type f -exec qmlformat -i {} +

# Format C/C++ source and header files with clang-format.
.PHONY: format-cpp
format-cpp:
	@command -v clang-format >/dev/null 2>&1 || { echo "Missing required tool: clang-format"; exit 1; }
	find $(CPP_DIR) -type f \( -name "*.c" -o -name "*.cc" -o -name "*.cpp" -o -name "*.h" -o -name "*.hh" -o -name "*.hpp" \) -exec clang-format -i {} +

# Format JSON/YAML/Markdown files with prettier.
.PHONY: format-prettier
format-prettier:
	@command -v prettier >/dev/null 2>&1 || { echo "Missing required tool: prettier"; exit 1; }
	find . -type f \
		\( -name "*.json" -o -name "*.yml" -o -name "*.yaml" -o -name "*.md" -o -name ".prettierrc" \) \
		-not -path "./.git/*" \
		-not -path "./build/*" \
		-print0 | xargs -0 -r prettier --write

# Run all formatting passes.
.PHONY: format
format: format-qml format-cpp format-prettier

# Linting
# Check QML formatting without changing files.
.PHONY: lint-qml-format
lint-qml-format:
	@command -v qmlformat >/dev/null 2>&1 || { echo "Missing required tool: qmlformat"; exit 1; }
	@status=0; \
	find $(PACKAGE_DIR) -name "*.qml" -type f -print0 | \
	xargs -0 -r -I{} sh -c 'tmp_file="$$(mktemp)"; qmlformat "$$1" > "$$tmp_file"; if ! cmp -s "$$1" "$$tmp_file"; then echo "QML format mismatch: $$1"; rm -f "$$tmp_file"; exit 1; fi; rm -f "$$tmp_file"' _ {} || status=1; \
	if [ "$$status" -ne 0 ]; then \
		echo "Run 'make format-qml' to fix QML formatting."; \
		exit 1; \
	fi

# Run semantic QML lint checks.
.PHONY: lint-qml
lint-qml: cpp-build
	@command -v qmllint >/dev/null 2>&1 || { echo "Missing required tool: qmllint"; exit 1; }
	find $(PACKAGE_DIR) -name "*.qml" -type f -print0 | xargs -0 -r qmllint

# Check C/C++ formatting without changing files.
.PHONY: lint-cpp
lint-cpp:
	@command -v clang-format >/dev/null 2>&1 || { echo "Missing required tool: clang-format"; exit 1; }
	find $(CPP_DIR) -type f \( -name "*.c" -o -name "*.cc" -o -name "*.cpp" -o -name "*.h" -o -name "*.hh" -o -name "*.hpp" \) -print0 | xargs -0 -r clang-format --dry-run --Werror

# Check JSON/YAML/Markdown formatting without changing files.
.PHONY: lint-prettier
lint-prettier:
	@command -v prettier >/dev/null 2>&1 || { echo "Missing required tool: prettier"; exit 1; }
	find . -type f \
		\( -name "*.json" -o -name "*.yml" -o -name "*.yaml" -o -name "*.md" -o -name ".prettierrc" \) \
		-not -path "./.git/*" \
		-not -path "./build/*" \
		-print0 | xargs -0 -r prettier --check

# Run all lint and formatting checks.
.PHONY: lint
lint: lint-qml-format lint-qml lint-cpp lint-prettier

# Installation
# Install/upgrade widget in local Plasma package store and replace any dev symlink.
.PHONY: install
install: cpp-build
	@install_path="$(HOME)/.local/share/plasma/plasmoids/$(WIDGET_ID)"; \
	if [ -L "$$install_path" ]; then \
		echo "Replacing development symlink with installed package: $$install_path"; \
		rm -f "$$install_path"; \
	fi; \
	if kpackagetool6 --type Plasma/Applet --show $(WIDGET_ID) >/dev/null 2>&1; then \
		echo "Upgrading existing package: $(WIDGET_ID)"; \
		kpackagetool6 --type Plasma/Applet --upgrade $(PACKAGE_DIR); \
	else \
		echo "Installing new package: $(WIDGET_ID)"; \
		kpackagetool6 --type Plasma/Applet --install $(PACKAGE_DIR); \
	fi

# Symlink local package into Plasma directory for live development updates.
.PHONY: dev-install
dev-install: cpp-build
	@echo "Creating symlink for development..."
	mkdir -p $(HOME)/.local/share/plasma/plasmoids/
	ln -sfn $(abspath $(PACKAGE_DIR)) $(HOME)/.local/share/plasma/plasmoids/$(WIDGET_ID)
	@echo "Linked $(WIDGET_ID) -> $(PACKAGE_DIR)"
	@echo "You can now use 'make test' or the Reload button in plasmoidviewer!"

# Upgrade an already installed widget package with local changes.
.PHONY: upgrade
upgrade: cpp-build
	kpackagetool6 --type Plasma/Applet --upgrade $(PACKAGE_DIR)

# Remove both dev symlink and installed widget package from local system.
.PHONY: uninstall
uninstall:
	rm -f $(HOME)/.local/share/plasma/plasmoids/$(WIDGET_ID)
	kpackagetool6 --type Plasma/Applet --remove $(WIDGET_ID)

# Packaging
# Create distributable .plasmoid archive from current package contents.
.PHONY: package
package: cpp-build
	mkdir -p $(BUILD_DIR)
	rm -f $(BUILD_DIR)/$(PLASMOID_FILE)

	# Zip package contents
	cd $(PACKAGE_DIR) && zip -r ../$(BUILD_DIR)/$(PLASMOID_FILE) .

	@echo ""
	@echo "Package built at: $(BUILD_DIR)/$(PLASMOID_FILE)"

# Delete all generated build artifacts for a clean workspace.
.PHONY: clean
clean:
	rm -rf $(BUILD_DIR) $(CPP_MODULE_DIR)
