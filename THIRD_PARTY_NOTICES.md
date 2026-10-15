# 第三方内容声明

本项目除源码外，还包含或使用了以下第三方内容。

## GeneralUser GS 音色库

- 文件：`assets/sf/GeneralUser-GS.sf2`
- 作者：S. Christian Collins
- 来源：https://www.schristiancollins.com/generaluser （GitHub：https://github.com/mrbumpy409/GeneralUser-GS）
- 许可证：GeneralUser GS License v2.0，全文见 `assets/sf/GeneralUser-GS-LICENSE.txt`。
  - 许可证允许用于个人或商业的音乐创作。
  - 允许在软件项目中使用并重新打包。
  - 作者要求不要直接链接到他的下载文件，可以链接到他的网站，或者自己提供一份副本。本仓库属于自己提供副本。

## AI 生成的图像

- 文件：`assets/raw/` 和 `assets/cut/` 下的全部图片（`assets/cut/` 下的 json 是程序生成的数据），以及 `docs/` 下的截图和封面。
- 生成方式：OpenAI codex CLI 的图像生成功能，提示词见 `src/gen_assets.py`。
- 以下几张图沿用自同系列的《三只小猪》项目，本仓库里没有它们的生成任务：
  - 纸纹 `paper_texture`、前景草丛 `fg_grass`
  - 云朵 `cloud1~3`、蝴蝶 `butterfly1~2`、小鸟 `bird`、树叶 `leaf1~4`、星星 `star`
- 这些图像的使用条款以 OpenAI 的服务条款为准。

## AI 合成的旁白

- 文件：`assets/voice/*.wav`
- 生成方式：MiniMax 语音合成接口（模型 speech-2.8-hd，系统音色 `Chinese (Mandarin)_Gentle_Senior`），文稿见 `src/narration.py`。
- 这些音频的使用条款以 MiniMax 的服务条款为准。

## 未包含的内容

- 字体“华文琥珀”（`STHUPO.TTF`）：代码中引用了它，但它是商业字体，本仓库不包含。详见 README 的“字体说明”。

## 故事

《小马过河》是流传很广的中文寓言故事，本片的旁白文稿是重新改编的版本。原作的作者和版权状况尚待确认，公开发布前请自行确认改编是否需要授权。
