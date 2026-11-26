# Tài liệu đồ án IT5404

| File | Nội dung |
|---|---|
| `bao-cao-it5404.docx` | Báo cáo bản Word, xuất từ Claude Doc (bản để nhóm cùng sửa): https://claude.ai/artifact/7KdrBpGJU9dgJkzrmjZwMC |
| `report/main.pdf` | Báo cáo bản PDF theo template Springer LNCS. Nguồn LaTeX: `report/main.tex`. Biên dịch: `cd report && tectonic main.tex` |
| `report/generated/` | Bảng số liệu sinh tự động bởi `experiments/report_tables.py` |
| `figures/` | Biểu đồ sinh bởi `experiments/analyze.py` |
| `figures/screens/` | Ảnh chụp minh chứng: Horizon, Prometheus Targets, Grafana |
| `screenshot.mjs` | Script chụp các ảnh trên bằng Chrome headless (puppeteer-core) |
| `demo-1c-api.sh` | Demo yêu cầu 1c: gọi REST API OpenStack bằng `curl` |

Số liệu thực nghiệm (chương 3) được điền tự động khi chạy xong `experiments/run-all.sh`.
