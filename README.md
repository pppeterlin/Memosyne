# Memosyne: The Personal Memory Framework

## 專案定位
Memosyne 是一個為 AI Agent 時代設計的「個人認知基礎設施」框架。它定義了個人碎片化數據如何透過 Agentic Pipeline 進行內化、結構化與檢索，旨在解決本地模型在個人上下文（Personal Context）理解上的斷層。

## 框架核心組件 (The Memosyne Stack)
- **Ingestion Layer (解耦傳入)**：不設限格式，負責將原始輸入（JSON, TXT, MD）轉換為待處理流。
- **Cognitive Engine (認知引擎)**：利用 LLM 執行主動整理、語義連結與知識補強（Proactive Probing）。
- **Retrieval & Compression (檢索與壓縮)**：整合 Vector DB 與 FlashRank，提供高品質、高密度的上下文輸出。
- **Interface Layer (MCP 接口)**：透過 Model Context Protocol 將記憶庫能力「插件化」。

## 為什麼選擇框架化？
- **工具中立**：你可以使用任何備份工具（如 Gemini Backup）作為輸入來源。
- **模型中立**：支援 Ollama, MLX, 或 Cloud APIs。
- **高度整合**：不僅是儲存，更定義了個人資料如何成為 IDE 或個人助理的「技能（Skill）」。