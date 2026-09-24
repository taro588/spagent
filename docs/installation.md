# SP AI Assistant 0.1.0 安装说明

## 官方插件目录

Adobe 官方文档说明，Windows 7.2+ 的用户资源根目录为：

C:\Users\<用户名>\Documents\Adobe\Adobe Substance 3D Painter

Python 插件目录：

C:\Users\<用户名>\Documents\Adobe\Adobe Substance 3D Painter\python\plugins

Legacy 路径：

C:\Users\<用户名>\Documents\Allegorithmic\Substance Painter\python\plugins

本版本安装器会优先使用已经存在的 Modern 目录，否则使用 Legacy；如果两者都不存在，则创建 Modern 目录。

## 安装内容

0.1.0 只安装真正由 Painter 发现的插件入口：

- sp_ai_assistant.py
- manifest.json

不会把 GitHub 工程源码目录整体复制进 Painter。

## 启用

1. 双击 Setup.exe。
2. 安装器完成文件验证后弹出成功提示。
3. 重新启动 Substance 3D Painter。
4. 打开 Python 菜单。
5. 启用 SP AI Assistant。
6. Dock 中点击“运行环境自检”。

## 为什么这样安装

Adobe 官方插件机制要求 Python 插件位于 Painter 的 Python plugin search path；标准插件需要 start_plugin() 和 close_plugin()。本项目因此把“开发源码”和“Painter 实际加载入口”分开，减少对 Painter 内部机制的依赖。

## 卸载

Windows 应用/程序和功能中卸载 SP AI Assistant，或使用安装器生成的卸载程序。

卸载只删除本插件安装的 sp_ai_assistant.py 和 manifest.json，不删除用户其他 Painter 插件。
