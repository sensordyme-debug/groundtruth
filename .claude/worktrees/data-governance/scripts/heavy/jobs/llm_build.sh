# Local LLM: build llama.cpp (CPU, native, Release) under ~/groundtruth-heavy/llm/llama.cpp. cmake + ninja from the venv.
set -e
export PATH="$HOME/groundtruth-heavy/.venv/bin:$PATH"
cd ~/groundtruth-heavy/llm/llama.cpp
echo "tag $(git describe --tags) commit $(git rev-parse --short HEAD)"
cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=ON -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF \
  -DLLAMA_BUILD_EXAMPLES=OFF 2>&1 | grep -iE "avx|fma|f16c|native|CPU|ggml version|commit" | head -20
cmake --build build -j 12 --target llama-server llama-bench 2>&1 | tail -3
ls -la build/bin/llama-server build/bin/llama-bench
build/bin/llama-server --version 2>&1 | tail -2
echo "build done"
