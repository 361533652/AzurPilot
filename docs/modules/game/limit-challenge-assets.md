# 限界挑战识别素材

素材目录 `assets/cn/limit_challenge/` 仅放图像，避免按钮生成器把文档当作识别资源。

素材来自用户提供的三张国服 1280×720 截图，原图保存在 `tests/fixtures/limit_challenge/`。普通 Button 通过 `dev_tools.button_region_editor.process_button_image()` 保留指定区域、将外部置黑；Template 通过 `process_template_image()` 裁剪。定义由 `uv run -m dev_tools.button_extract` 生成，不手写 `assets.py`。

| 素材 | 原图 | 保留范围 (x1, y1, x2, y2) |
| --- | --- | --- |
| `ui/CAMPAIGN_MENU_GOTO_LIMIT_CHALLENGE` | `menu.png` | `(525, 599, 626, 626)` |
| `ui/CAMPAIGN_MENU_GOTO_LIMIT_CHALLENGE.BUTTON` | `menu.png` | `(497, 588, 627, 636)` |
| `ui/LIMIT_CHALLENGE_CHECK` | `easy.png` | `(120, 14, 235, 43)` |
| `CHALLENGE_START` | `easy.png` | `(1037, 632, 1209, 678)` |
| `CHALLENGE_PREPARATION` | `preparation.png` | `(1057, 559, 1134, 592)` |
| `CHALLENGE_PREPARATION.BUTTON` | `preparation.png` | `(1048, 557, 1238, 609)` |
| `SURFACE_FLEET` | `preparation.png` | `(33, 615, 114, 641)` |
| `LEVEL_1` | `easy.png` | `(27, 420, 244, 480)` |
| `LEVEL_2` | `easy.png` | `(27, 520, 244, 580)` |
| `LEVEL_3` | `easy.png` | `(27, 620, 244, 680)` |
| `TEMPLATE_LEVEL_SELECTED` | `easy.png` | `(30, 438, 52, 461)` |
| `TEMPLATE_LEVEL_LOCKED` | `easy.png` | `(73, 542, 227, 571)` |

`LEVEL_*` 只定义行区域与点击范围，不把窥探文字搬成其他难度的识别模板。当前以共用选中菱形和锁定文字确认行状态；审视与洞察的真实选中图、不同月份的主题以及实机完整战斗仍待追加截图验收。编队名称是玩家可改字段，不用作页面识别。
