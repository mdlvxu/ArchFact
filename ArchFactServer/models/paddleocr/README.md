# PP-OCRv6_small

检测和识别权重放在这个目录，供 PaddleOCR 3.x worker 离线加载。

- `PP-OCRv6_small_det/`
- `PP-OCRv6_small_rec/`

权重不进 git。缺失时在 `ArchFactServer` 下用本机 `ppocr3` 的 Python 执行：

```
C:/Users/<USER>/miniconda3/envs/ppocr3/python.exe scripts/download_ppocrv6_small.py
```

下载源是百度 BOS，同时会复制到 `%USERPROFILE%\.paddlex\official_models`。
