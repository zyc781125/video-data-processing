# 浏览器端视频片段筛选与裁剪工具

## 启动

在 `data-process` 环境安装 Python 依赖：

```bash
conda run -n data-process python -m pip install -r requirements.txt
```

系统需要可执行的 `ffmpeg` 和 `ffprobe`。Linux 运行 `./start_linux.sh`，Windows 运行 `start_windows.bat`，然后访问 `http://127.0.0.1:5000`。

输入目录会递归扫描并按自然文件名排序。扫描在后台进行，每处理 10 个视频就更新列表；已出现的视频可以立即打开。更换输入或输出目录会切换项目，旧项目的进度仍保存在 `project/progress.sqlite` 中，切回原目录即可继续。

播放视频或拖动进度条时会更新当前帧。按 `R` 可让当前视频从头开始播放。点击“设置起点”或“设置终点”时，后端按实际逐帧时间戳确认帧号；暂停时也可用左右键微调。片段使用闭区间帧号，导出为无音频 H.264 MP4；元数据写入输出目录。
