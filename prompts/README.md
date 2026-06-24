# 测试 Prompt 集

配套压测场景见 [../docs/test-scenarios.md](../docs/test-scenarios.md)。

---

## 一、输入长度分级（测 TTFT / Prefill 性能）

用于 [场景 2.5](../docs/test-scenarios.md#25-不同-prompt-长度对比)：**对比输入长度对首 Token 延迟的影响**。
三个文件输出预期相近，唯一变量是输入长度，因此能干净地隔离出 Prefill 的影响。

| 文件 | 输入长度（约） | 模拟场景 |
|------|---------------|----------|
| [short.txt](short.txt) | ~30 token | 简单问答、短指令 |
| [medium.txt](medium.txt) | ~300 token | 带背景的复杂任务 |
| [long.txt](long.txt) | ~2000 token | RAG / 长文档问答 |

```bash
# 三档对比，其余参数完全一致
python bench.py chat -f config/config.yaml --prompt-file prompts/short.txt  -c 10 -n 200 --max-tokens 256 --stream
python bench.py chat -f config/config.yaml --prompt-file prompts/medium.txt -c 10 -n 200 --max-tokens 256 --stream
python bench.py chat -f config/config.yaml --prompt-file prompts/long.txt   -c 10 -n 200 --max-tokens 256 --stream
```

> 关注 `ttft_p50`：输入越长，TTFT 应越大。若增长过快，说明 Prefill 是瓶颈。

---

## 二、真实业务场景（测典型负载下的性能）

用于评估模型在**真实业务负载**下的表现。每个场景的输入/输出特征不同。

| 文件 | 场景 | 特征 | 建议 max-tokens |
|------|------|------|----------------|
| [biz-customer-service.txt](biz-customer-service.txt) | 智能客服 | 短输入短输出，要求低延迟 | 200 |
| [biz-translation.txt](biz-translation.txt) | 翻译 | 中输入中输出 | 300 |
| [biz-code-generation.txt](biz-code-generation.txt) | 代码生成 | 短输入长输出 | 1024 |

```bash
# 客服场景：关注低延迟，模拟高并发
python bench.py chat -f config/config.yaml --prompt-file prompts/biz-customer-service.txt -c 20 -n 400 --max-tokens 200 --stream

# 翻译场景
python bench.py chat -f config/config.yaml --prompt-file prompts/biz-translation.txt -c 10 -n 200 --max-tokens 300 --stream

# 代码生成：长输出，注意调大超时
python bench.py chat -f config/config.yaml --prompt-file prompts/biz-code-generation.txt -c 10 -n 100 --max-tokens 1024 --stream -t 60
```

---

## 三、使用要点

1. **对比测试时，除被测变量外其余参数必须完全一致**（见场景文档原则 1）。
2. **测输入长度影响**用第一组（short/medium/long），它们刻意保持输出相近。
3. **测真实业务表现**用第二组，每个场景用自己推荐的 `max-tokens`。
4. 长输出场景（代码生成）记得用 `-t` 调大超时，避免误判为失败。

---

## 四、自定义 Prompt

直接在本目录新建 `.txt` 文件，用 `--prompt-file prompts/你的文件.txt` 引用即可。
注意：文件内容会被 `.strip()` 去除首尾空白后整体作为单条 user 消息发送。
