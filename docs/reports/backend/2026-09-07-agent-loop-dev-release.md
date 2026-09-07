# Agent loop 首批 dev 发布记录

用户在本地开发验收后明确要求「那你发布吧，我看看效果」。本次通过现有 master → dev 流水线发布 API 与 UI，未使用其他发布环境。

## 发布身份

- 仓库：`HyxiaoGe/fusion`；开发分支：`codex/agent-loop-reliability`。
- PR [#49](https://github.com/HyxiaoGe/fusion/pull/49)，审查/PR CI head：`7a20d127cd9b848eed5d0f572fc6e82ed8f5405f`。
- master 合并/实际部署 SHA：`11906526a49907e70b8c0133342b5524e2c57faf`；合并时间：北京时间 2026-09-07 09:06:52。
- 前后端部署完成时间：北京时间 2026-09-07 09:17:21。
- 已读取 dev 的 API/UI accepted-release 台账，二者 current_sha 与上面 SHA 相同；运行容器的 image ID 和 digest 引用均与台账对应。

| 应用 | 运行镜像内容 ID | 仓库 digest | 运行状态 |
| --- | --- | --- | --- |
| API | `sha256:9acb18582402d161348c45d90aff3f278e87d9755d8dee757bc6f1a841a9a0e1` | `sha256:6fe4a915435272f911d3a34bc97f1434f3d76e3c7edda3deca24e7e9439dd73b` | 运行中，重启 0 次 |
| UI | `sha256:07be5713da6e000e2f97bf3dcb86988fce23addee0d3f7551644627f92b0b5c2` | `sha256:001f2b9ad298128a5d97a5249364e962a46dc636f9b50754241b6bf904dbc14a` | 运行中，重启 0 次 |

## 门禁与实际检查

- 发布前后端目标 `557 passed + 194 subtests`、前端 `192 passed`、根契约 `67 tests OK`，改动静态检查与 production build 通过；完整 tsc 保留原有 25 处错误，无新增。独立审查及最终取消缺口复审无剩余 P0/P1。
- [PR CI](https://github.com/HyxiaoGe/fusion/actions/runs/34071451174)：API validation、UI validation、Workflow security validation、Detect changes、Fusion required gate 均成功。
- [master CI](https://github.com/HyxiaoGe/fusion/actions/runs/34071945145)：上述五个门禁均成功；已核对合并提交的 backend/frontend/ops 内容与 PR head 完全一致。
- [dev 部署](https://github.com/HyxiaoGe/fusion/actions/runs/34071945449)：API Windows 测试/镜像推送、dev 替换、镜像身份/健康/冒烟、accepted-release 记录均成功；随后 UI Windows 测试/镜像推送、dev 替换、候选健康和浏览器冒烟、accepted-release 记录均成功。未触发失败回滚。
- 独立只读验证：API `healthy`，数据库和 Redis 均 `connected`；实际 API 镜像的详情 DTO 包含 output_provenance、emitted/suppressed/replaced 与 tool_retracted；UI 主机 3004 端口 `/chat/new` 返回 200。
- 公开地址 [Fusion 新对话](https://fusion.seanfield.org/chat/new) 返回 HTTP 200；页面实际引用的 `/_next/static/chunks/3454-5780951feab5ba71.js` 包含 outputProvenance 和「正文输出归因」文案。

## 登录态验收边界与查看方式

发布和部署级冒烟已完成。登录态真实新对话、工具结果/停止链路、轨迹交互、刷新恢复以及用户浏览器的 network/console 尚未验收，不能用部署冒烟或静态资源检查代替。

开始时既有 Fusion Chrome 标签被另一任务占用；部署完成后再次获取时标签已不可用，重新读取浏览器状态明确返回 Mac 已锁屏、无法自动解锁。未新开浏览器目标，未绕过占用或锁屏，也未获取/伪造用户 token。

用户解锁后刷新 Fusion，发起一条新对话，在 trajectory 中打开模型节点即可查看「正文输出归因」。旧对话没有这批元数据，显示未知是预期行为；显示全部处置类型仍需相应真实场景。

本记录留在本地版本库，发布业务代码已由 PR #49 合并并部署。
