#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <random>
#include <string>
#include <vector>

extern "C" int LLVMFuzzerTestOneInput(const uint8_t* data, size_t size);

namespace {

std::vector<std::string> load_corpus(int argc, char** argv) {
    std::vector<std::string> corpus;
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg.rfind("-", 0) == 0) {
            continue;
        }

        std::ifstream input(arg + "/seed.txt", std::ios::binary);
        if (!input) {
            continue;
        }
        corpus.emplace_back(
            std::istreambuf_iterator<char>(input),
            std::istreambuf_iterator<char>());
    }

    if (corpus.empty()) {
        corpus.emplace_back("0");
    }
    return corpus;
}

int max_total_time(int argc, char** argv) {
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        const std::string prefix = "-max_total_time=";
        if (arg.rfind(prefix, 0) == 0) {
            return std::max(1, std::atoi(arg.substr(prefix.size()).c_str()));
        }
    }
    return 10;
}

}  // namespace

int main(int argc, char** argv) {
    auto corpus = load_corpus(argc, argv);
    const int seconds = max_total_time(argc, argv);
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(seconds);

    std::mt19937 rng(12345);
    std::uniform_int_distribution<int> byte_dist(0, 255);
    std::uniform_int_distribution<int> size_dist(0, 64);
    size_t runs = 0;

    while (std::chrono::steady_clock::now() < deadline) {
        std::string input = corpus[runs % corpus.size()];
        if (runs % 3 != 0) {
            input.resize(static_cast<size_t>(size_dist(rng)));
            for (char& ch : input) {
                ch = static_cast<char>(byte_dist(rng));
            }
        }

        LLVMFuzzerTestOneInput(reinterpret_cast<const uint8_t*>(input.data()), input.size());
        ++runs;
    }

    std::cerr << "local fallback fuzzer completed " << runs << " runs in "
              << seconds << " seconds\n";
    return 0;
}
