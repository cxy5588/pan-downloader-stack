# 一键部署指南

本指南展示如何将 PanSou 搜索（8081）、Flask 下载器（8082）和 AList（8083）一次性部署到任意服务器。

## 1. 准备环境

- Linux x86_64 主机（建议 2C4G+）。
- 已安装 Docker 24+ 与 docker compose（`docker compose version` 可用）。
- 获取以下账号信息：
  - `BDUSS`、`STOKEN`（百度网盘账号）
  - `QUARK_COOKIE`（登录夸克网页后复制完整 Cookie）
  - `QUARK_TARGET_FOLDER_FID`（夸克中用于转存 PanSou 结果的目录 FID，通常为“影视下载”的 fid）

## 2. 配置 .env

1. 复制示例：`cp .env.example .env`
2. 打开 `.env`，填写下列内容：
   - `BDUSS`、`STOKEN`
   - `QUARK_COOKIE`
   - `QUARK_TARGET_FOLDER_FID`
   - 如需自定义端口或下载目录，也可在 `.env` 中调整 `PANSOU_PORT`、`DOWNLOADER_PORT`、`ALIST_PORT`、`SHIPIN_DATA` 等。

> `ALIST_PASSWORD` 默认与 `ALIST_ADMIN_PASSWORD` 相同，可自行修改，但需保持一致。

## 3. 启动服务

```bash
docker compose up -d --build
```

compose 会启动三个容器：

| 服务 | 端口（默认） | 说明 |
|------|--------------|------|
| pansou | 8081 | PanSou 搜索 API |
| downloader | 8082 | Flask 聚合下载器（指向 `/mnt/shipin`） |
| alist | 8083 | AList 管理面板（用户名 `admin`，密码来自 `.env`） |

日志查看：

```bash
docker compose logs -f downloader
```

停止：`docker compose down`

## 4. 首次配置 AList

1. 浏览器访问 `http://<服务器IP>:8083`，使用 `.env` 中的 `ALIST_ADMIN_PASSWORD` 登录。
2. 在 AList 后台添加夸克驱动，填入 `Cookie`、根目录、挂载路径 `/夸克网盘` 等信息。确保“影视下载”目录可见。

## 5. 使用

- 打开 `http://<服务器IP>:8082` 执行搜索与一键下载。
- 下载文件会保存到主机上的 `./data/shipin`（或 `.env` 中 `SHIPIN_DATA` 指定的目录），可直接挂载到电视或 NAS。

如需更新：拉取最新代码后执行 `docker compose up -d --build` 即可滚动升级。
