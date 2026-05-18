# Git 操作规范 (git_sop)

通过 shell_run 执行 git 命令。禁止注册为独立工具，保持工具集精简。

## 常用命令

### 查看状态
```
shell_run: git status --short
shell_run: git diff
shell_run: git diff --cached   (暂存区)
shell_run: git diff -- path/to/file  (指定文件)
shell_run: git log --oneline -10
```

### 提交流程
```
shell_run: git add file1.py file2.py   (指定文件暂存)
shell_run: git add -A                  (全部暂存，慎用)
shell_run: git commit -m "描述"
```

### 分支
```
shell_run: git branch
shell_run: git checkout -b feature-xxx
shell_run: git checkout main
```

## 安全规则

**禁止操作（shell_run 会拦截）：**
- `git reset --hard` — 不可逆丢弃
- `git push --force` — 覆盖远程历史
- `git clean -f` — 删除未跟踪文件

**需要 ask_user 确认的操作：**
- `git push` — 推送到远程前确认
- `git merge` — 合并前确认
- `git rebase` — 变基前确认
- 删除分支 `git branch -D`

## 注意事项

- cwd 参数：git 命令默认在 workspace 下执行，如果项目根目录才是 git 仓库，用 `cwd` 指定为项目根
- Windows 路径：用正斜杠 `/` 或双反斜杠 `\\`
- 编码：commit message 用 UTF-8，Windows 下 git log 输出可能有编码问题，加 `--encoding=utf-8`
