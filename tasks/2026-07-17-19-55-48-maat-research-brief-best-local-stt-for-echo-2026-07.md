# Maat research brief — best local STT for Echo (2026-07)

*Generated: 2026-07-17T19:55:48.054Z | Job #28 | Synthesizer: ollama-mac (gemma4:31b-cloud) | Sources: 26 cited / 30 provided*

---
## Executive Summary

This research brief evaluates local Speech-to-Text (STT) replacements for the Echo system, focusing on balancing accuracy, latency, and Windows 11 compatibility. While `faster-whisper` remains the recommended runtime for NVIDIA GPUs due to its VRAM efficiency via int8 quantization [https://www.promptquorum.com/powe], the landscape has shifted toward streaming-native architectures like Moonshine v2 and Kyutai STT-1B to better support latency-critical voice assistants. NVIDIA’s Parakeet and Canary families offer superior accuracy and speed over Whisper `base`, though they introduce complex PyTorch integration challenges. To optimize for an RTX 5080 environment, the analysis prioritizes models that can bypass CPU-bound PyTorch constraints through independent runtimes. The final recommendation focuses on high-accuracy models for proper nouns that maintain a high real-time factor (RTFx) for live interaction.

---

## Whisper Family Current State

The current landscape of the Whisper family for local deployment is dominated by the `faster-whisper` implementation, which utilizes the CTranslate2 engine to optimize inference. For a Windows-based voice assistant, `faster-whisper` is the recommended runtime for NVIDIA GPUs as it provides significant throughput increases and VRAM reductions via int8 quantization [https://www.promptquorum.com/power-local-llm/local-whisper-stt-comparison-2026].

### Performance and Accuracy Analysis: Large-v3, Turbo, and Distil

When evaluating the transition from the `base` model to larger variants to improve accuracy—particularly for proper nouns—the `large-v3` family offers a substantial leap in capability, though it introduces significant latency and resource trade-offs.

#### large-v3 (Standard)
The `large-v3` model is the most accurate of the standard Whisper family, with a reported Word Error Rate (WER) of 2.5% [https://www.promptquorum.com/power-local-llm/local-whisper-stt-comparison-2026]. However, it is computationally expensive. In benchmarks processing 13 minutes of audio, the standard `faster-whisper` implementation of this model took approximately 52 seconds (both fp16 and int8) [https://github.com/SYSTRAN/faster-whisper/issues/1030]. While it provides the highest accuracy baseline, its latency makes it less suitable for real-time interaction compared to the turbo or distilled variants.

#### large-v3-turbo
The `large-v3-turbo` model is identified as the 2026 "sweet spot" for balancing accuracy and speed [https://www.digitalapplied.com/blog/local-speech-to-text-whisper-self-hosted-transcription-2026]. It contains 809 million parameters and requires approximately 6 GB of VRAM [https://www.digitalapplied.com/blog/local-speech-to-text-whisper-self-hosted-transcription-2026]. 

In direct performance benchmarks on a GPU, `faster-large-v3-turbo` significantly outperformed other variants:
*   **Transcription Speed:** It processed 13 minutes of audio in 19.155s (fp16) and 19.591s (int8) [https://github.com/SYSTRAN/faster-whisper/issues/1030].
*   **Accuracy:** It achieved the lowest WER of 1.919% on the Librispeech clean val split, outperforming both the standard `large-v3` and the distilled version [https://github.com/SYSTRAN/faster-whisper/issues/1030].

#### distil-large-v3
The `distil-large-v3` model is designed for maximum speed, offering approximately 6× the speed of the standard model with a minor accuracy trade-off, typically a WER increase of about 1% [https://www.digitalapplied.com/blog/local-speech-to-text-whisper-self-hosted-transcription-2026]. 

In the 13-minute audio benchmark, `faster-distil-large-v3` recorded transcription times of 26.126s (fp16) and 22.537s (int8), with a WER of 2.392% [https://github.com/SYSTRAN/faster-whisper/issues/1030]. While faster than the standard `large-v3`, it is slower and less accurate than the `large-v3-turbo` variant.

### Latency and VRAM Footprint

The resource requirements and latency of these models vary significantly based on the precision used (fp16 vs. int8) and the hardware.

*   **VRAM Efficiency:** `faster-whisper` utilizes CTranslate2 int8 quantization to reduce VRAM usage by approximately 40% [https://www.promptquorum.com/power-local-llm/local-whisper-stt-comparison-2026]. For example, `large-v3` can run on an RTX 4070 using approximately 2.5 GB of VRAM while achieving ~12× real-time speed [https://www.promptquorum.com/power-local-llm/local-whisper-stt-comparison-2026].
*   **Real-Time Constraints:** A critical caveat for voice assistant integration is that Whisper is optimized for batch transcription. Its fixed 30-second input window creates high compute waste and latency for short clips, which can make it impractical for live voice applications compared to streaming-native models like Moonshine [https://modelslab.com/blog/audio-generation/moonshine-vs-whisper-asr-real-time-speech-2026].
*   **Hardware Scaling:** Benchmarks indicate that high-end consumer GPUs like the RTX 4090 provide the lowest time costs, while older hardware like the RTX 2080Ti is significantly slower (3.7× the cost of a 4090) [https://github.com/openai/whisper/discussions/918].

### Summary Comparison Table

| Model Variant | Parameters | VRAM (Approx.) | WER (Librispeech) | 13m Audio Time (Faster-Whisper) |
| :--- | :--- | :--- | :--- | :--- |
| **large-v3** | 1.55B | ~2.5 GB (int8) | 2.5% - 2.88% | ~52s |
| **large-v3-turbo** | 809M | ~6 GB | 1.919% | ~19s |
| **distil-large-v3** | (Distilled) | (Low) | 2.392% | ~22-26s |

No source in this corpus addresses the specific accuracy of these models regarding "proper nouns" beyond general WER metrics; however, the `large-v3-turbo` model demonstrates the lowest overall WER among the tested `faster-whisper` variants [https://github.com/SYSTRAN/faster-whisper/issues/1030].

## NVIDIA Parakeet and Canary Family (NeMo)

The NVIDIA NeMo ecosystem provides two primary families of speech-to-text (STT) models: the Parakeet series, focused on high-throughput ASR, and the Canary series, which evolves into hybrid ASR/LLM architectures. For a Windows-based voice assistant, these models offer a significant accuracy and speed upgrade over Whisper `base`, though they introduce substantial integration challenges regarding Python environments and CUDA dependencies.

### Canary-Qwen-2.5B: Accuracy and Hybrid Capabilities
Canary-Qwen-2.5B is a hybrid model combining a FastConformer speech encoder with a Qwen-family LLM decoder (~2.5B parameters) [https://github.com/huggingface/blog/pull/3262/changes]. Unlike standard ASR models, it unifies transcription with text reasoning, allowing for immediate downstream summarization and question-answering within a single pipeline [https://github.com/huggingface/blog/pull/3262/changes].

*   **Accuracy:** The model is highly optimized for English, reporting a Word Error Rate (WER) of approximately 5.6% to 5.63% on standard benchmarks [https://github.com/huggingface/blog/pull/3262/changes, https://github.com/zsxkib/cog-nvidia-canary-qwen-2.5b]. One independent tutorial test reported a slightly higher global WER of 5.91% [https://github.com/FurkanGozukara/Stable-Diffusion/wiki/Best-Open-Source-Subtitle-Generator-Canary-Qwen-25B-Whisper-Full-Guide].
*   **Performance:** Inference speed is exceptionally high, with reports of 418x real-time speed (processing one minute of audio in 0.14 seconds) [https://github.com/zsxkib/cog-nvidia-canary-qwen-2.5b], though other benchmarks place it at 46x faster than real-time [https://github.com/FurkanGozukara/Stable-Diffusion/wiki/Best-Open-Source-Subtitle-Generator-Canary-Qwen-25B-Whisper-Full-Guide].
*   **Constraints:** The model is primarily English-focused [https://github.com/huggingface/blog/pull/3262/changes]. It supports audio files up to 2 hours in length, but its LLM analysis capabilities are most effective when transcripts remain under 1,000 words [https://github.com/zsxkib/cog-nvidia-canary-qwen-2.5b].

### Parakeet Family: Throughput and Efficiency
The Parakeet family consists of English ASR models in 0.6B and 1.1B parameter sizes, available in CTC, RNNT, and TDT (Token-and-Duration Transducer) variants [https://developer.nvidia.com/blog/nvidia-speech-and-translation-ai-models-set-records-for-speed-and-accuracy/].

*   **Parakeet-TDT:** This architecture skips blank predictions, making the 1.1B model 64% faster than the second-best Parakeet model on the Hugging Face leaderboard [https://developer.nvidia.com/blog/nvidia-speech-and-translation-ai-models-set-records-for-speed-and-accuracy/].
*   **Comparative Accuracy:** Parakeet-TDT-0.6B-v3 is reported to have a WER of 6.34%, slightly outperforming Whisper v3's 6.43% [https://www.digitalapplied.com/blog/local-speech-to-text-whisper-self-hosted-transcription-2026]. It offers significantly higher throughput, approximately 49× that of Whisper [https://www.digitalapplied.com/blog/local-speech-to-text-whisper-self-hosted-transcription-2026].
*   **Language Support:** While the primary Parakeet models are English-centric, some versions support up to 25 European languages [https://www.digitalapplied.com/blog/local-speech-to-text-whisper-self-hosted-transcription-2026].

### Windows Integration and Environment Conflicts
Integrating NeMo models into a Windows 11 Python 3.11 virtualenv presents significant friction, particularly for systems requiring a CPU-only `torch` installation in the primary application environment.

#### The CUDA-Torch Conflict
Standard NeMo ASR deployment requires `nemo_toolkit[asr]` and a CUDA-enabled PyTorch stack (e.g., `cu121`) [https://github.com/KoljaB/RealtimeSTT/blob/master/docs/engines/parakeet-nemo.md]. Because the user's primary venv must remain CPU-only, a direct installation of NeMo is not "venv-safe" and would cause a conflict with the existing `torch` installation.

#### Integration Strategies
To avoid these conflicts, three distinct deployment patterns are identified:

1.  **Separate Server Process (Recommended):**
    *   **Cog/Docker:** Canary-Qwen-2.5B can be deployed via Docker and Cog, effectively isolating the CUDA-torch dependencies from the host Windows environment [https://github.com/zsxkib/cog-nvidia-canary-qwen-2.5b].
    *   **vLLM Server:** There are efforts to support Canary-Qwen-2.5B via vLLM. While early versions suffered from transcript deletions for audio longer than 10 seconds, a claimed fix was released in July 2026 via a separate repository [https://github.com/NVIDIA-NeMo/NeMo/issues/14541].
    *   **Subprocess Mode:** For applications like Whisper TTS Premium, using "subprocess mode" is recommended to prevent VRAM and RAM leaks during transcription [https://github.com/FurkanGozukara/Stable-Diffusion/wiki/Best-Open-Source-Subtitle-Generator-Canary-Qwen-25B-Whisper-Full-Guide].

2.  **ONNX Runtime (Venv-Safe Alternative):**
    *   The **WhisperScript** implementation allows Parakeet TDT 0.6b v3 to run on Windows via **ONNX Runtime** [https://github.com/NVIDIA-NeMo/Speech/discussions/15491]. This provides both CPU and CUDA variants without requiring a full NeMo/PyTorch installation, potentially bypassing the CUDA-torch conflict.

3.  **WSL2 (Development/Testing):**
    *   Due to frequent package resolution failures on native Windows, NeMo ASR is primarily supported on Linux; Windows users are strongly advised to use WSL2 for model testing [https://github.com/KoljaB/RealtimeSTT/blob/master/docs/engines/parakeet-nemo.md].

### Summary of Technical Requirements for Windows
| Model | Primary Runtime | Windows Compatibility | VRAM/RAM Note |
| :--- | :--- | :--- | :--- |
| **Canary-Qwen-2.5B** | Docker/Cog / vLLM | High (via Container) | ~5GB model size [https://github.com/FurkanGozukara/Stable-Diffusion/wiki/Best-Open-Source-Subtitle-Generator-Canary-Qwen-25B-Whisper-Full-Guide] |
| **Parakeet-TDT** | ONNX / NeMo | Medium (Native) / High (WSL2) | Higher memory/longer startup than "tiny Whisper" [https://github.com/KoljaB/RealtimeSTT/blob/master/docs/engines/parakeet-nemo.md] |
| **Parakeet-TDT** | WhisperScript | High (Native) | Uses ONNX Runtime [https://github.com/NVIDIA-NeMo/Speech/discussions/15491] |

## Alternative Models: Moonshine, Kyutai, and Others

As of mid-2026, the landscape for local Speech-to-Text (STT) has shifted from batch-processing models (like Whisper) toward streaming-native architectures designed for latency-critical voice assistants. The primary alternatives to the Whisper family are Moonshine v2 and Kyutai STT-1B, alongside a new wave of multimodal "Omni" models from IBM, NVIDIA, and Alibaba.

### Moonshine v2: Ergodic Streaming ASR
Moonshine v2 is specifically engineered for edge applications and live voice interfaces, addressing the linear growth of time-to-first-token (TTFT) found in traditional Transformer encoders [https://arxiv.org/html/2602.12241v1].

*   **Architecture and Latency:** Moonshine v2 replaces global self-attention with sliding-window self-attention, reducing computational complexity from quadratic $\mathcal{O}(T^2)$ to linear $\mathcal{O}(Tw)$ [https://arxiv.org/html/2602.12241v1]. This "ergodic" design—which lacks absolute or relative positional embeddings—allows for translation-invariant computations in time [https://arxiv.org/html/2602.12241v1]. With a right-context window of $w_{right}=4$ frames, the model achieves an algorithmic lookahead of only 80ms [https://arxiv.org/html/2602.12241v1].
*   **Performance and Accuracy:** Benchmarks indicate that Moonshine Medium Streaming (245M parameters) can outperform Whisper Large v3 (1,500M parameters) in both speed and accuracy [https://modelslab.com/blog/audio-generation/moonshine-vs-whisper-asr-real-time-speech-2026]. Specifically, on a MacBook Pro, Moonshine Medium Streaming recorded a Word Error Rate (WER) of 6.65% compared to Whisper Large v3's 7.44%, while being approximately 105x faster (107ms vs 11,286ms latency) [https://modelslab.com/blog/audio-generation/moonshine-vs-whisper-asr-real-time-speech-2026].
*   **VRAM and Footprint:** The model family is highly scalable, offering "tiny, small, and medium" variants [https://arxiv.org/html/2602.12241v1]. The smallest deployments can be as small as 26MB, making it viable for extremely constrained hardware [https://github.com/moonshine-ai/moonshine-v2].
*   **Windows and Integration:** Moonshine v2 is explicitly compatible with Windows [https://github.com/moonshine-ai/moonshine-v2]. Crucially for the Maat system constraints, it supports multiple backends including **ONNX runtime**, TensorFlow, and JAX, in addition to the default Torch backend [https://github.com/chrisputzu/comparison-moonshine-vs-faster-whisper-tiny-en]. This suggests it can be integrated without requiring a CUDA-enabled Torch installation.

### Kyutai STT-1B: Delayed Streams Modeling
Kyutai's STT-1B (`kyutai/stt-1b-en_fr`) utilizes a streaming-native architecture based on Delayed Streams Modeling (DSM) and the Moshi audio tokenization framework [https://github.com/imprasukjain/Kyutai-STT].

*   **Latency and Scalability:** Kyutai STT-1B is positioned as a direct alternative to Whisper for real-time use, claiming to reduce response times from 5 seconds (typical of Whisper's batch design) to 0.5 seconds [https://github.com/imprasukjain/Kyutai-STT]. In high-throughput environments, a single NVIDIA H100 can process 400 uninterrupted real-time streams at a 1.0x real-time factor (RTF) using a batch size of 128 [https://github.com/kyutai-labs/delayed-streams-modeling/issues/151].
*   **VRAM and Efficiency:** The model reports a 40% reduction in peak memory usage compared to Whisper [https://github.com/imprasukjain/Kyutai-STT]. For extremely constrained environments, the model has been successfully quantized to 4-bit GGUF format to fit within a 4GB memory limit for WASM browser deployments [https://github.com/kyutai-labs/delayed-streams-modeling/issues/121].
*   **Windows and Integration:** The model requires Python 3.12+ and a CUDA-enabled GPU [https://github.com/imprasukjain/Kyutai-STT]. While it can be run via a Rust server for high concurrency, the primary inference library is `moshi` [https://github.com/imprasukjain/Kyutai-STT]. No source in this corpus addresses a native Windows ONNX or CTranslate2-style runtime that would bypass the need for CUDA-torch.
*   **Caveats:** Its language support is significantly narrower than Whisper, currently limited to English and French with automatic detection [https://github.com/imprasukjain/Kyutai-STT].

### 2025-2026 Multimodal and Specialized Releases
Several new model families were introduced via Microsoft Foundry in May 2026, moving toward "Omni" architectures that integrate audio, video, and text [https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/now-in-foundry-ibm-granite-4-1-nvidia-nemotron-nano-omni-and-qwen3-6-35b-a3b/4516858].

*   **IBM Granite Speech 4.1-2B:** A multilingual speech recognition model part of the Granite 4.1 family [https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/now-in-foundry-ibm-granite-4-1-nvidia-nemotron-nano-omni-and-qwen3-6-35b-a3b/4516858].
*   **NVIDIA Nemotron-3-Nano-Omni-30B-A3B:** A Mamba2-Transformer Hybrid MoE model. While it has 31B total parameters, only ~3B are activated per forward pass [https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/now-in-foundry-ibm-granite-4-1-nvidia-nemotron-nano-omni-and-qwen3-6-35b-a3b/4516858]. It supports audio up to 1 hour and provides word-level timestamps for precise alignment [https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/now-in-foundry-ibm-granite-4-1-nvidia-nemotron-nano-omni-and-qwen3-6-35b-a3b/4516858]. It is available in BF16, FP8, and NVFP4 variants [https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/now-in-foundry-ibm-granite-4-1-nvidia-nemotron-nano-omni-and-qwen3-6-35b-a3b/4516858].
*   **Qwen3.6-35B-A3B:** A hybrid Gated DeltaNet and MoE architecture (3B activated parameters) designed for agentic reasoning [https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/now-in-foundry-ibm-granite-4-1-nvidia-nemotron-nano-omni-and-qwen3-6-35b-a3b/4516858].

**Comparison Summary for Maat Constraints:**

| Model | Latency Profile | VRAM / Size | Windows/Python Integration |
| :--- | :--- | :--- | :--- |
| **Moonshine v2** | Ultra-low (80ms lookahead) | Very Low (Tiny: 26MB) | High (Windows support; ONNX runtime available) |
| **Kyutai STT-1B** | Low (0.5s response) | Moderate (1B params; 4-bit GGUF available) | Moderate (Requires Python 3.12+ and CUDA GPU) |
| **Nemotron Omni** | Not specified (Streaming) | High (31B total / 3B active) | Complex (Requires specialized NVIDIA hardware/FP8/NVFP4) |
| **Granite Speech** | Not specified | Moderate (2B params) | Not specified in corpus |

Regarding the specific Maat constraint of a CPU-only Torch environment, Moonshine v2 is the most viable candidate due to its explicit support for the ONNX runtime [https://github.com/chrisputzu/comparison-moonshine-vs-faster-whisper-tiny-en]. Kyutai STT-1B's reliance on the `moshi` library and CUDA-enabled GPUs [https://github.com/imprasukjain/Kyutai-STT] makes it a poor fit unless deployed as a separate server process. No source in this corpus addresses the specific Windows compatibility or VRAM footprint of SenseVoice or Voxtral.

## Hugging Face Open ASR Leaderboard Analysis

To identify a replacement for Whisper `base` that meets the specific constraints of the Echo system—namely high accuracy for proper nouns, low latency, and a decoupled CUDA runtime—an analysis of the Open ASR and Far-Field ASR (FFASR) leaderboards is required. The following analysis cross-references model accuracy (WER) against inference speed (RTFx) and architectural suitability.

### High-Accuracy Candidates and the "Benchmaxxing" Caveat
The Open ASR Leaderboard ranks models primarily by Mean Word Error Rate (WER). As of the most recent data, the top-performing models are:
*   **AutoArk-AI/ARK-ASR-3B**: 4.76 WER [https://huggingface.co/datasets/hf-audio/open-asr-leaderboard]
*   **OpenMOSS-Team/MOSS-Transcribe-preview-2B**: 4.87 WER [https://huggingface.co/datasets/hf-audio/open-asr-leaderboard]
*   **CohereLabs/cohere-transcribe-03-2026**: 5.42 WER [https://huggingface.co/datasets/hf-audio/open-asr-leaderboard]
*   **AutoArk-AI/ARK-ASR-0.6B**: 5.53 WER [https://huggingface.co/datasets/hf-audio/open-asr-leaderboard]

However, these benchmark scores must be viewed with caution. Hugging Face explicitly identified "benchmaxxing"—the practice of optimizing models for specific benchmark scores without improving real-world robustness—leading to the introduction of private datasets from Appen Inc. and DataoceanAI in May 2026 to combat test-set contamination [https://huggingface.co/blog/open-asr-leaderboard-private-data]. Furthermore, AssemblyAI reports that real-world WER is often 2–3x worse than clean benchmarks [https://www.assemblyai.com/blog/top-open-source-stt-options-for-voice-applications].

### Latency and Throughput: Architectural Trade-offs
For a real-time voice assistant, the "natural" response threshold is sub-300ms end-to-end latency [https://www.assemblyai.com/blog/top-open-source-stt-options-for-voice-applications]. The leaderboards reveal a sharp divide between accuracy and speed based on decoder architecture:

*   **LLM-Based Decoders:** Models combining Conformer encoders with Large Language Model (LLM) decoders (e.g., IBM Granite-Speech-3.3-8B, Microsoft Phi-4-Multimodal-Instruct) achieve the highest English transcription accuracy [https://huggingface.co/blog/open-asr-leaderboard]. However, these are significantly slower and likely exceed the latency requirements for a real-time assistant [https://huggingface.co/blog/open-asr-leaderboard].
*   **CTC and TDT Decoders:** Connectionist Temporal Classification (CTC) and Token-and-Duration Transducer (TDT) decoders provide 10–100× faster throughput than LLM decoders [https://huggingface.co/blog/open-asr-leaderboard]. 
    *   **NVIDIA Parakeet-TDT (1.1B):** This model uses a novel architecture to skip blank predictions, making it 64% faster than the second-best Parakeet model on the leaderboard while maintaining top accuracy [https://developer.nvidia.com/blog/nvidia-speech-and-translation-ai-models-set-records-for-speed-and-accuracy/].
    *   **NVIDIA Parakeet CTC 1.1B:** Offers an RTFx of 2793.75, compared to only 68.56 for Whisper Large v3, representing a massive increase in inference speed with only moderate WER degradation [https://huggingface.co/blog/open-asr-leaderboard].

### Far-Field Performance (FFASR)
Because the Echo system operates in a real-world environment, the FFASR Leaderboard (launched June 2026) provides a more critical metric than the standard Open ASR board. The FFASR benchmark simulates 14 furnished rooms to test reverberation and background noise [https://huggingface.co/blog/ffasr-leaderboard].

The primary finding is a significant performance gap: WER at low Signal-to-Noise Ratio (SNR < 6 dB) is consistently several times higher than near-field WER [https://huggingface.co/blog/ffasr-leaderboard]. Candidates for the Echo system must be evaluated on the FFASR Pareto front plots, which map the trade-off between average WER and RTFx on NVIDIA L4 GPUs [https://huggingface.co/blog/ffasr-leaderboard]. Supported architectures on this leaderboard include Whisper variants, IBM Granite Speech, Cohere Transcribe, and Wav2Vec2 [https://huggingface.co/blog/ffasr-leaderboard].

### VRAM and Integration Constraints
The Echo system requires a model that can run CUDA without a `torch` dependency in the main virtualenv. 

*   **NVIDIA Parakeet/Canary:** These models are highly optimized for NVIDIA hardware and can be deployed via NVIDIA NIM (cloud, workstation, or PC) or NVIDIA Riva [https://developer.nvidia.com/blog/nvidia-speech-and-translation-ai-models-set-records-for-speed-and-accuracy/]. This aligns with the system's requirement for a separate local server process. 
*   **Faster-Whisper:** While the native OpenAI Whisper is batch-only, Faster-Whisper is 4x faster and uses lower VRAM [https://www.assemblyai.com/blog/top-open-source-stt-options-for-voice-applications]. It utilizes CTranslate2, which is torch-independent, satisfying the requirement to run CUDA without `torch` in the primary environment.
*   **Vosk:** Listed as CPU efficient with native streaming support, though its WER (12–35%) is significantly higher than the top leaderboard candidates [https://www.assemblyai.com/blog/top-open-source-stt-options-for-voice-applications].

### Summary of Candidate Suitability

| Model Family | Accuracy (WER) | Latency (RTFx) | Integration Fit | Note |
| :--- | :--- | :--- | :--- | :--- |
| **NVIDIA Parakeet (TDT/CTC)** | High | Ultra-High | High (via NIM/Riva) | Best for real-time; TDT is 64% faster than others [https://developer.nvidia.com/blog/nvidia-speech-and-translation-ai-models-set-records-for-speed-and-accuracy/]. |
| **Faster-Whisper** | Moderate | Moderate | High (CTranslate2) | Reliable baseline; lower VRAM than native Whisper [https://www.assemblyai.com/blog/top-open-source-stt-options-for-voice-applications]. |
| **LLM-Decoders (Phi-4/Granite)** | Ultra-High | Low | Low | Too slow for real-time voice agents [https://huggingface.co/blog/open-asr-leaderboard]. |
| **Wav2Vec2** | Moderate | Good | Moderate | Strong fine-tuning capabilities for proper nouns [https://www.assemblyai.com/blog/top-open-source-stt-options-for-voice-applications]. |

## Ranked Shortlist and Recommendation

Based on the requirement for a real-time voice assistant on Windows 11 with a CPU-only PyTorch environment, the following models are ranked by their suitability for replacing Whisper `base`. The primary selection criteria are latency for live interaction, VRAM efficiency on an RTX 5080, and the ability to bypass the `torch` CPU constraint via independent runtimes or separate server processes.

### Ranked Model Shortlist

| Rank | Model | WER | Latency / Speed | VRAM (RTX 5080) | Windows Integration Route |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **1** | **Faster-Whisper (large-v3-turbo)** | 1.919% [https://github.com/SYSTRAN/faster-whisper/issues/1030] | ~19.1s per 13m audio [https://github.com/SYSTRAN/faster-whisper/issues/1030] | ~2.5 GB (large-v3) [https://www.promptquorum.com/power-local-llm/local-whisper-stt-comparison-2026] | CTranslate2 (Torch-independent CUDA) [https://github.com/huggingface/blog/pull/3262/changes] |
| **2** | **Moonshine (v2 Medium Streaming)** | 6.65% [https://modelslab.com/blog/audio-generation/moonshine-vs-whisper-asr-real-time-speech-2026] | 107ms (Streaming) [https://modelslab.com/blog/audio-generation/moonshine-vs-whisper-asr-real-time-speech-2026] | Not specified (Edge-optimized) [https://modelslab.com/blog/audio-generation/moonshine-vs-whisper-asr-real-time-speech-2026] | Local Python/C++ runtime [https://modelslab.com/blog/audio-generation/moonshine-vs-whisper-asr-real-time-speech-2026] |
| **3** | **Canary-Qwen-2.5B** | 5.6% [https://github.com/huggingface/blog/pull/3262/changes] | 418x real-time [https://github.com/zsxkib/cog-nvidia-canary-qwen-2.5b] | Not specified (Requires CUDA) [https://github.com/zsxkib/cog-nvidia-canary-qwen-2.5b] | Separate Docker/Cog server process [https://github.com/zsxkib/cog-nvidia-canary-qwen-2.5b] |
| **4** | **Parakeet TDT 0.6b v3** | 6.34% [https://www.digitalapplied.com/blog/local-speech-to-text-whisper-self-hosted-transcription-2026] | ~49x Whisper v3 throughput [https://www.digitalapplied.com/blog/local-speech-to-text-whisper-self-hosted-transcription-2026] | Not specified (High memory) [https://github.com/KoljaB/RealtimeSTT/blob/master/docs/engines/parakeet-nemo.md] | ONNX Runtime via WhisperScript [https://github.com/NVIDIA-NeMo/Speech/discussions/15491] |

---

### Detailed Analysis of Candidates

#### Faster-Whisper (large-v3-turbo)
Faster-Whisper is the most viable drop-in replacement due to its use of **CTranslate2**, which allows it to utilize CUDA without requiring a CUDA-enabled PyTorch installation [https://github.com/huggingface/blog/pull/3262/changes]. This directly satisfies the hard constraint of the CPU-only `torch` virtualenv. 

The `large-v3-turbo` variant is identified as the 2026 "sweet spot" [https://www.digitalapplied.com/blog/local-speech-to-text-whisper-self-hosted-transcription-2026], delivering a state-of-the-art WER of 1.919% on Librispeech clean val [https://github.com/SYSTRAN/faster-whisper/issues/1030]. In terms of hardware efficiency, the `large-v3` model utilizes approximately 2.5 GB of VRAM on an RTX 4070 (and thus similarly on an RTX 5080) when using int8 quantization, which reduces VRAM usage by roughly 40% [https://www.promptquorum.com/power-local-llm/local-whisper-stt-comparison-2026].

#### Moonshine (v2)
Moonshine is specifically engineered for live voice assistants, unlike Whisper, which is optimized for batch processing [https://modelslab.com/blog/audio-generation/moonshine-vs-whisper-asr-real-time-speech-2026]. It solves the "compute waste" problem of Whisper's fixed 30-second windows by using flexible input windows and streaming caching [https://modelslab.com/blog/audio-generation/moonshine-vs-whisper-asr-real-time-speech-2026]. 

The latency advantage is extreme: the Medium Streaming model recorded a latency of 107ms, compared to 11,286ms for Whisper Large V3 [https://modelslab.com/blog/audio-generation/moonshine-vs-whisper-asr-real-time-speech-2026]. While its WER (6.65%) is higher than Faster-Whisper's turbo variant, it is more accurate than Whisper Large V3 (7.44%) despite having 6x fewer parameters [https://modelslab.com/blog/audio-generation/moonshine-vs-whisper-asr-real-time-speech-2026].

#### NVIDIA Canary-Qwen-2.5B
This hybrid model combines a FastConformer speech encoder with a Qwen-family LLM decoder [https://github.com/huggingface/blog/pull/3262/changes]. It is highly efficient, processing a 1-minute recording in 0.14 seconds (418x real-time speed) [https://github.com/zsxkib/cog-nvidia-canary-qwen-2.5b]. 

The primary advantage is the integration of ASR and text reasoning (summarization, QA) in one pipeline [https://github.com/huggingface/blog/pull/3262/changes]. However, it is limited to English [https://github.com/zsxkib/cog-nvidia-canary-qwen-2.5b] and requires a separate server process (Docker/Cog) to avoid conflicts with the main application's environment [https://github.com/zsxkib/cog-nvidia-canary-qwen-2.5b].

#### Parakeet TDT 0.6b v3
Parakeet offers superior throughput—approximately 49x that of Whisper v3 [https://www.digitalapplied.com/blog/local-speech-to-text-whisper-self-hosted-transcription-2026]—and a competitive WER of 6.34% [https://www.digitalapplied.com/blog/local-speech-to-text-whisper-self-hosted-transcription-2026]. 

Integration on Windows is problematic; the `nemo_toolkit` typically requires Linux or WSL2 due to package resolution failures [https://github.com/KoljaB/RealtimeSTT/blob/master/docs/engines/parakeet-nemo.md]. While **WhisperScript** provides a "one-click" Windows integration via ONNX Runtime [https://github.com/NVIDIA-NeMo/Speech/discussions/15491], this is a standalone application rather than a library integration, making it less flexible for a custom voice assistant.

---

### Final Recommendation

**Primary Recommendation: Faster-Whisper (`large-v3-turbo`)**
Faster-Whisper is the recommended choice because it is the only high-accuracy model that natively solves the environment constraint. By utilizing **CTranslate2**, it achieves CUDA acceleration without requiring `torch` to be installed with CUDA support [https://github.com/huggingface/blog/pull/3262/changes]. It provides the lowest WER (1.919%) [https://github.com/SYSTRAN/faster-whisper/issues/1030] and a very low VRAM footprint (~2.5 GB) [https://www.promptquorum.com/power-local-llm/local-whisper-stt-comparison-2026], ensuring the RTX 5080 remains available for other assistant tasks.

**Runner-Up: Moonshine (v2 Medium Streaming)**
If the project prioritizes "instant" feel over absolute transcription accuracy, Moonshine is the superior alternative. Its streaming architecture eliminates the latency spikes associated with Whisper's 30-second windowing, reducing latency from seconds to milliseconds [https://modelslab.com/blog/audio-generation/moonshine-vs-whisper-asr-real-time-speech-2026]. It is the "clear default" for live voice interfaces [https://modelslab.com/blog/audio-generation/moonshine-vs-whisper-asr-real-time-speech-2026].

---

## Sources
- https://arxiv.org/html/2602.12241v1
- https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/now-in-foundry-ibm-granite-4-1-nvidia-nemotron-nano-omni-and-qwen3-6-35b-a3b/4516858
- https://github.com/huggingface/blog/pull/3262/changes
- https://github.com/NVIDIA-NeMo/Speech/discussions/15491
- https://github.com/KoljaB/RealtimeSTT/blob/master/docs/engines/parakeet-nemo.md
- https://github.com/SYSTRAN/faster-whisper/issues/1030
- https://github.com/openai/whisper/discussions/918
- https://github.com/moonshine-ai/moonshine
- https://github.com/chrisputzu/comparison-moonshine-vs-faster-whisper-tiny-en
- https://github.com/zsxkib/cog-nvidia-canary-qwen-2.5b
- https://github.com/NVIDIA-NeMo/NeMo/issues/14541
- https://github.com/moonshine-ai/moonshine-v2
- https://github.com/kyutai-labs/delayed-streams-modeling/issues/151
- https://github.com/imprasukjain/Kyutai-STT
- https://github.com/kyutai-labs/delayed-streams-modeling/issues/121
- https://github.com/FurkanGozukara/Stable-Diffusion/wiki/Best-Open-Source-Subtitle-Generator-Canary-Qwen-25B-Whisper-Full-Guide
- https://www.assemblyai.com/blog/top-open-source-stt-options-for-voice-applications
- https://www.promptquorum.com/power-local-llm/local-whisper-stt-comparison-2026
- https://modelslab.com/blog/audio-generation/moonshine-vs-whisper-asr-real-time-speech-2026
- https://www.digitalapplied.com/blog/local-speech-to-text-whisper-self-hosted-transcription-2026
- https://developer.nvidia.com/blog/nvidia-speech-and-translation-ai-models-set-records-for-speed-and-accuracy/
- https://huggingface.co/blog/ffasr-leaderboard
- https://huggingface.co/datasets/hf-audio/open-asr-leaderboard
- https://huggingface.co/blog/open-asr-leaderboard
- https://huggingface.co/blog/open-asr-leaderboard-private-data
