# 两人协作约定

## 各自的工作副本

项目负责人管理仓库与合并，师弟通过自己的 GitHub 账号加入协作者。每人 clone 到自己的目录；共享服务器也不要共用同一个开发目录、活动分支或复核数据库。

仓库地址：

```bash
git clone https://github.com/Currylo/space-time-memory-agent.git
cd space-time-memory-agent
git config user.name "自己的名字"
git config user.email "自己的提交邮箱"
```

当前仓库只建立了协作文档，实际源码导入仍待完成。Git 只复制受跟踪文件，不复制视频、模型、虚拟环境和数据库；按数据交接文档另行准备。

各自配置 GitHub 登录或 SSH 凭据，不共享密码、令牌和私钥。共用系统账号时在各自仓库使用上面的局部身份配置。

## 一项任务一个分支

先领取或创建 Issue，写清任务边界、负责人、依赖和验收要求。从最新 main 建任务分支：

```bash
git switch main
git pull --ff-only
git switch -c feat/任务简名
```

分支可用 feat/、fix/、docs/、test/ 前缀。开始新任务前先处理已有未提交改动。

改动完成后检查差异，只添加本次相关文件：

```bash
git status --short
git diff
git add 本次修改的文件路径
git diff --cached --stat
git diff --cached
git commit -m "说明这次解决的问题"
git push -u origin 当前分支名
```

将占位文字换成实际值。提交前核对数据、凭据、第三方许可和大文件；.gitignore 不能自动保护已经被跟踪的文件。

## Pull Request 与验收

在 GitHub 从任务分支向 main 创建 PR，填写：

- 解决的问题与关联 Issue。
- 改动后的行为、影响范围和数据/配置变化。
- 实际执行的测试与结果；明确未运行的检查及原因。
- 页面改动附桌面/窄屏截图，数据或规则改动附版本与评价证据。

由另一位开发者检查关键改动。建议由项目负责人完成合并；这是团队约定，尚未配置为 GitHub 强制分支规则。

main 的目标是可复现、关键流程可验证。当前源码尚未导入，所以还未达到这个目标。合并后双方同步 main；发生冲突时先理解双方改动再解决，不用强推覆盖共享历史。

## 验收记录与实验

- 功能链路、规则效果、现场可用性分别记录。
- 固定运行编号、配置版本、源数据标识与提交 SHA。
- 模型派生结果与独立人工标注分别保存。
- 浏览器复核流程使用独立测试数据库，真实结论由实际复核者提交。
- 当前公开仓库只保存经过审查的必要说明；大型产物按清单交接。
