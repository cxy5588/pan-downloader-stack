# 🚀 Pan-Downloader-Stack: 全自动影视搜索下载器

[![GitHub License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Docker Support](https://img.shields.io/badge/Docker-Supported-emerald)](https://www.docker.com/)
[![Built With](https://img.shields.io/badge/Built%20With-Open%20Source-orange)](https://github.com/cxy5588/pan-downloader-stack)

> **🌟 这是一套“全家桶”级别的部署方案！**
> 深度整合了 **PanSou 搜索** 与 **AList 云盘管理**，通过 Docker Compose 让你在几分钟内拥有一个从 **资源搜索**、**云端转存** 到 **本地挂载播放** 的完整链路。

---

## 🛠️技术溯源与致谢 (Power By)

本方案核心功能依托于以下优秀的开源项目：

* 🔍 **[PanSou](https://github.com/fish2018/pansou)**：提供强大的网盘资源索引与前端搜索界面。
* 🗂️ **[AList](https://github.com/AlistGo/alist)**：负责多网盘挂载、文件管理及 WebDAV 协议支持。

---

## 🌟核心功能特性

* 🛰️ **全网搜刮**：直接调用 PanSou 的强大引擎，网盘资源一网打尽。
* ⚡ **秒速转存**：告别繁琐的手动转存，通过 本项目实现夸克网盘资源的一键抓取。
* 🌐 **多端兼容**：通过 WebDAV让你可以在电脑、手机、电视端（如 Kodi, PotPlayer） 观看。
* 📦 **极简部署**：无需在 Linux 上配置复杂的各种依赖，Docker 环境下一键拉起。

---

## 🚀快速上手流程

### 1️⃣环境准备
```bash
git clone [https://github.com/cxy5588/pan-downloader-stack.git](https://github.com/cxy5588/pan-downloader-stack.git)
cd pan-downloader-stack
```
### 2️⃣ 凭据配置 (Critical 🔑)
编辑 `.env` 文件，请务必完成以下核心设置：

* **📁 夸克网盘必备设置**：
    * 你需要在夸克网盘根目录**手动新建**一个用于转存的文件夹（例如命名为 `影视下载`）。
    * 获取该文件夹的 `fid` 并填入 `.env` 中的 `QUARK_TARGET_FOLDER_FID`。
    * **注意**：该文件夹相当于“落地仓”，若不建立或 ID 错误，转存将失败。
* **💾本地存储路径**：
    * 默认下载路径映射在宿主机的 `./data/shipin`（相对于项目根目录）。
    * 若需更改存储位置，请修改 `.env` 中的 `SHIPIN_DATA` 变量。

**需要填写的凭据：**
- **夸克网盘**: `QUARK_COOKIE` & `目录FID`

### 3️⃣ 一键启动
```bash
docker compose up -d --build
```

---

## 📺端口导航

| 服务名称 | 访问地址 | 功能描述 |
| :--- | :--- | :--- |
| **🔍 资源搜索** | `http://IP:8081` | 访问 PanSou 界面，搜索你想看的一切 |
| **⚡ 一键下载** | `http://IP:8082` | 在本页面搜索资源，并且点击一键下载 |
| **📂文件管理** | `http://IP:8083` | AList 终端，挂载夸克下载资源 |

---

## 🏗️系统架构图

```mermaid
graph LR
    User((用户)) -->|1. 搜索| PanSou[PanSou 搜索端]
    PanSou -->|2. 点击下载| Flask[Flask 自动调度]
    Flask -->|3. 文件同步| AList[AList 文件管理]
    Cloud -->|4. 调用 API下载到本地| Cloud[夸克云端]
    AList -->|5. 播放| TV((电视/电脑))
```

---

## 🤝 声明与支持

* **免责声明**：本项目仅供技术交流与个人学习使用，资源版权归原作者所有。
* **Star 赞助**：如果你喜欢这个整合方案，请给个 **Star** ? 支持一下！
* **技术致谢**：感谢 [@fish2018](https://github.com/fish2018) 和 [@AlistGo](https://github.com/AlistGo) 提供的核心技术支持。

---

©2026 [cxy5588](https://github.com/cxy5588). Build with ❤️ for the open source community.