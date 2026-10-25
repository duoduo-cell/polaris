# 演示视频

成片：https://youtu.be/ogbxFrPQ2Is （2:38，1080p，英文旁白和字幕，无背景音乐）。字幕文件：[polaris-demo.en.srt](polaris-demo.en.srt)。

画面全部来自合成演示（端口 3010），岗位、面经、公司名和档案都是虚构的；界面是中文。旁白用 Windows 自带的 Zira 语音离线合成。

## 怎么重新生成

需要：合成演示在本机跑着（见 [docs/DEMO.md](../docs/DEMO.md)），系统装有 Edge，以及一份带 ffmpeg 的 Python 环境（`pip install imageio-ffmpeg`）。下面的 `WORK` 是任意工作目录。

1. 把本目录的文件复制到 `WORK`，改 `narration.json` 里的旁白。
2. 逐句合成语音：`powershell -File WORK/tts.ps1 -Dir WORK`
3. 拼音轨、算字幕时间：`python WORK/build_audio.py WORK`，会打印每段时长和总长（要在 3 分钟以内）。
4. 录屏：`python WORK/record.py WORK`。它会先把演示恢复干净，并给 J-2 记一段投递进度，然后录对话、跟进卡、竞争力、投递和追踪四段。
5. 片头、问题说明、片尾三张卡片：用 Edge 把 `cards.html` 里的三个 `.card` 各截一张 1920×1080 的图，命名为 `s0_title.png`、`s1_problem.png`、`s6_close.png`（`FLOW_SVG` 换成 `site/img/flow-en.svg` 的路径）。
6. 合成：`python WORK/compose.py WORK <ffmpeg 路径>`。录屏段按旁白时长调速，场景之间淡入淡出，字幕烧进画面，输出 `polaris-demo.mp4`。

录完记得在演示的“跟进”页点“重来一遍”。
