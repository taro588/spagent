# SP AI Assistant

Standalone AI assistant plugin for Adobe Substance 3D Painter.

## Phase 0.1
- Dock UI
- Painter runtime self-check
- Plugin manifest
- Provider/model configuration placeholders
- Compatibility capability layer
- Windows installer source (Inno Setup)
- Tests

This project is intentionally independent from GameArt AI Toolkit.

## Installation
The intended release artifact is a Windows Setup.exe. The installer detects Substance 3D Painter and installs the plugin into the user's Painter plugin directory without modifying Painter core files.

## Development
Phase 0.1 establishes the safe plugin shell and diagnostics layer. AI execution is introduced in Phase 0.2.
