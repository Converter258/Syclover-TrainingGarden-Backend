# VM101 自动更新

本目录属于 fork 的 `deploy/vm101` 分支。公网 VPS 每 15 分钟通过 GitHub API 和源码归档同步 K4per 两个上游仓库的 `main` 与 Converter258 两个 fork 的 `deploy/vm101`；VM101 每 30 分钟从 VPS 读取交付仓库。更新器以最新上游源码为基础，逐文件应用生产分支相对初始版本的变动，在独立 release 构建并检查，通过后切换 `/opt/training-garden/current`。

VM101 无法稳定访问 GitHub，因此源码归档交付仓库位于 VPS `/srv/training-garden/delivery.git`。VM101 使用仅可读取交付仓库的 `tg-source` SSH 账号。原公网 Nginx、frps、VM100 frpc 和堡垒入口均不在更新流程内。

更新器在上游同时修改生产覆盖文件、构建失败、数据库结构变化或健康检查失败时保留上一版；数据库结构变化时新 release 已暂存，需人工审核迁移和回退方案。切换前调用现有备份脚本，并在备份副本上执行迁移。旧镜像与旧 release 保留。

状态：`/var/lib/training-garden/updater/status.json`；日志：`journalctl -u training-garden-update.service`；镜像日志：`journalctl -u training-garden-source-sync.service`。人工触发：VM101 上 `sudo systemctl start training-garden-update.service`；VPS 上 `sudo systemctl start training-garden-source-sync.service`。

生产修复应先提交到两个 fork 的 `deploy/vm101` 分支。常规功能更新仍由 K4per 上游 `main` 提供。更新器读取的四个分支 SHA 会写入每次发布的 `RELEASE_MANIFEST.txt`。
