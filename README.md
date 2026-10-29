# WuwaTappo Helper / WuwaTappo 小助手

> **非官方项目。** 本软件不是库洛游戏（Kuro Games）、《鸣潮》（Wuthering Waves）或 Wuwa Tappo 的官方产品，也未获得其授权、认可或背书。
>
> 自动化操作可能违反第三方用户协议，存在账号、活动资格和虚拟财产风险。请自行判断是否使用。

一个面向 Windows 的 Wuwa Tappo 桌面小助手：模拟键盘连发、在宝箱就绪时打开宝箱，并提供桌面值守、对话、成就和统计功能。

作者：**让你破费的泪**

---
## 更新说明

- [1.3.2 正式版](docs/releases/1.3.2.md)
- [1.4.1 极简版](docs/releases/1.4.1.md)
## 功能

- 使用 `SendInput` 模拟 **F13–F24** 键盘按键，不用鼠标连点；默认 F24。
- 通过 UI Automation 读取 Wuwa Tappo 的累计操作和宝箱按钮。
- 宝箱就绪时通过内部 `Invoke` 打开，不需要手动定位坐标。
- 自动开箱默认关闭，可通过 **F11** 或主界面开关启用；一次只处理一个宝箱。
- 主界面三条计数：本次、本软件累计、当前应用。
- 桌面值守：普通对话版 / 简洁气泡版，可切换置顶，默认右下角。
- 男/女漂泊者切换、可拖动角色、点击角色回应。
- 品质台词、星级成就、线索币、隐藏成就。
- 数据保存在本地，不联网、不上传。

## 环境要求

- Windows 10 / 11
- Wuwa Tappo 已安装并能正常运行
- 便携版需要管理员权限
- 从源码运行需要 Python 3.11+，推荐 Python 3.13

## 安装与运行

### 便携版

1. 解压整个 Release 压缩包，保留 `_internal` 文件夹。
2. 双击 `WuwaTappo 小助手.exe`。
3. 如果 Windows 出现 **“Windows 已保护你的电脑”**，点“更多信息”→“仍要运行”。
4. 在 UAC 提示中选择“是”。
5. 首次启动阅读免责声明，并按提示查看“更多”里的“使用前看我”。
6. 打开 Wuwa Tappo，确认桌宠显示后，回到小助手按 **F8** 开始。

> 便携包只应包含 `WuwaTappo 小助手.exe` 和 `_internal`。不要只复制 exe。

### 从源码运行

```powershell
python -m pip install -r requirements.txt
python app.py
```

程序会主动请求管理员权限；开发调试可使用 `--no-elevate`，但不建议日常使用。

## 快捷键

| 快捷键 | 功能 |
|---|---|
| **F8** | 开始 / 停止自动连发 |
| **F3** | 只停止 |
| **F1** | 打开宝箱一 |
| **F2** | 打开宝箱二 |
| **F10** | 主面板 / 桌宠切换 |
| **F11** | 自动开箱 开 / 关 |

- 连发输出键：F13–F24，默认 F24。
- 本软件不使用 F6、F7、F9；这些键可能属于 Wuwa Tappo 自身功能，请勿用它们操作桌宠。

## 首次使用

1. 打开 Wuwa Tappo 并登录。
2. 保持模拟键为 F24，按 F8 以低速率试跑。
3. 确认“当前应用”计数会增加，按 F3 能立即停止。
4. 需要自动开箱时再按 F11；默认不会自动开箱。
5. 需要收起主面板时按 F10；双击角色或右键也能返回主面板。

## 设置

“更多 → 设置”可调整：

- 当前应用计数刷新间隔：1–60 秒，默认 10 秒；
- 点击数据语句间隔：15–600 秒，默认 120 秒；
- 简洁气泡显示时长：1–15 秒，默认 5 秒。

## 数据与隐私

- 不联网、不上传账号、截图或点击记录。
- 配置、统计和日志保存在：

```text
%APPDATA%\WuwaAutoChest
```

- 删除该文件夹即可重置设置、统计和成就。
- 不读取游戏账号密码。

## 从源码构建

```powershell
python -m pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

构建结果：

```text
dist\WuwaAutoChest\WuwaTappo 小助手.exe
```

便携包应在 Release 中分发，不要提交到 Git 仓库。

## 已知限制

- 需要管理员权限；拒绝 UAC 时无法正常读取累计数和宝箱。
- Wuwa Tappo 更新后，UI Automation 的控件类名可能变化，届时需要适配。
- Windows SmartScreen 会对未签名 exe 提示；本项目没有代码签名证书。
- 宝箱品质识别依赖 Wuwa Tappo 的界面类名，蓝/紫/金品质未逐一实机验证。
- 自动化操作存在账号风险；不保证奖励到账或账号安全。

## 版权与免责声明

- 本项目代码由 **让你破费的泪** 编写，使用 MIT License。
- `assets/` 中的角色、宝箱、图标和 UI 素材版权归 **库洛游戏（Kuro Games）** 及相关权利人所有，**不包含在 MIT License 中**。
- 本项目与库洛游戏、鸣潮、Wuwa Tappo 没有隶属、授权或合作关系。
- 素材仅用于本机个人学习、界面展示和兼容性用途；不得商用、二次售卖或冒充官方。
- 详细第三方声明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 作者

**让你破费的泪**

## License

代码：MIT License，见 [LICENSE](LICENSE)。  
第三方素材与商标不适用 MIT License，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
