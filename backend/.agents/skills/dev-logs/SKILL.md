---
name: dev-logs
description: 按时间窗口和请求标识只读调查 Fusion dev 日志，定位服务错误或链路中断。
---

# Dev 日志调查

通过已有 `ssh dev` 访问，先确认目标容器及时间窗口。用户时间按 `Asia/Shanghai`，Docker 时间筛选使用带时区的 ISO 时间或明确的相对窗口。

```bash
ssh dev 'docker ps --filter name=fusion --format "{{.Names}} {{.Status}}"'
ssh dev 'docker logs --since 15m --timestamps --tail 300 fusion-api 2>&1'
```

按 conversation/message/run/round/tool 标识缩小查询，必要时扩大一个相关窗口。保留异常上下文与堆栈，不默认屏蔽 LiteLLM、ImportError、Traceback 或退避信息；只有证据证明与本次无关才排除。

容器名、端口与部署位置以当前 workflow 和运行信息为准。不要读取全量日志后才截尾，也不输出凭据、用户消息正文或其他敏感内容。

日志能证明服务端发生了什么，不能替代页面表现或网络实际到达。只读调查不重启、不改配置、不创建会话；需要主动复现时沿当前授权判断，不反复申请已获得的权限。
