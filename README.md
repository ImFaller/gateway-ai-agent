# gateway-ai-agent

模拟 AI 网关配置网页，嵌入大模型对话能力，支持策略管理、策略编排、模型配置、日志与监控等功能。

## 本地运行

### 1. 下载项目

方式一：在 GitHub 页面点击 `Code` -> `Download ZIP`，解压后进入项目目录。

方式二：使用 Git 克隆：

```bash
git clone https://github.com/ImFaller/gateway-ai-agent.git
cd gateway-ai-agent
```

### 2. 安装依赖

建议使用 Python 3.10 或更高版本。

```bash
pip install -r requirements.txt
```

### 3. 启动服务

```bash
python -m uvicorn system.app:app --host 0.0.0.0 --port 8099
```

### 4. 打开网页

浏览器访问：

```text
http://localhost:8099
```

### 5. 首次使用

首次进入页面会提示修改默认密码。

默认密码：

```text
123456
```

请按页面提示设置新密码。

### 6. 配置模型

进入「模型配置」页面，填写模型服务信息和 API Key。

DeepSeek 示例：

```text
提供商：DeepSeek
模型：deepseek-chat
API 地址：https://api.deepseek.com/v1
API Key：你的 API Key
```

保存并测试连接成功后，对话功能即可使用。

## 重要说明

- `.gitignore` 已经提交到仓库，用来避免把本地 `.env`、缓存文件、虚拟环境、日志等内容上传到 GitHub。
- `.gitignore` 不会影响别人下载和运行项目。GitHub 下载包会包含所有已提交的源码文件、`requirements.txt`、`.env.example` 和 README。
- 如果通过网页「模型配置」填写 API Key，通常不需要手动创建 `.env`。
- 模型配置、密码、运行时新增策略等数据会保存在本机临时目录中，不会提交到 GitHub。
- 如果需要使用 `.env` 配置默认模型，可以复制 `.env.example` 为 `.env`，再填写自己的 API Key。

## 常用入口

| 功能 | 地址 |
| --- | --- |
| 管理页面 | http://localhost:8099 |
| 健康检查 | http://localhost:8099/api/v1/health |
| 策略列表 | http://localhost:8099/api/v1/strategies |

## 常用 Git 操作

查看当前修改：

```bash
git status
```

提交本地修改：

```bash
git add .
git commit -m "说明这次改了什么"
```

上传到 GitHub：

```bash
git push
```

从 GitHub 同步最新代码：

```bash
git pull
```
