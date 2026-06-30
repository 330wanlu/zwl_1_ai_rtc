# 常见问题与解决方法 (FAQ)

## RTC / Token 相关

问题一：浏览器报了 `token_error` 错误
解决：更新 RTC Token 就好了。打开 `server_python/scenes/Custom.json`，检查 `RTCConfig` 中的 `Token`、`AppId`、`RoomId`、`UserId` 是否正确或已过期，更新最新 Token 即可。

---

如有其他问题，欢迎联系我们反馈。