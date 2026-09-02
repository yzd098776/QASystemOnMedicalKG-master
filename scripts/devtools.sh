# coding: sh
# Makefile 配套小工具：按 pidfile / 端口安全停止进程。
# 注意：不要用 `pkill -f "<含端口的命令行>"` —— make 的 recipe shell 自身命令行
# 也包含同样的字符串，pkill 会把当前 shell 一起杀掉（本项目实测踩过）。

# kill_pidfile <path>：pid 文件存在且进程存活则 TERM→等待→KILL
kill_pidfile() {
  local pf="$1"
  [ -f "$pf" ] || return 0
  local pid
  pid=$(cat "$pf" 2>/dev/null)
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    for _ in 1 2 3 4 5; do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$pf"
}

# kill_on_port <port>：按监听端口找 pid 并杀（兜底，覆盖 pidfile 丢失场景）
kill_on_port() {
  local port="$1" pid
  pid=$(ss -tlnp 2>/dev/null | grep -E ":${port} " | grep -oP 'pid=\K[0-9]+' | head -1)
  if [ -n "${pid:-}" ] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    sleep 2
    kill -9 "$pid" 2>/dev/null || true
  fi
}
