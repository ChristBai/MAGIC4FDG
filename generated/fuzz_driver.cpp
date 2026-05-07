#include <cstddef>
#include <cstdint>
#include <string>

#include "tiny.h"

extern "C" int LLVMFuzzerTestOneInput(const uint8_t* data, size_t size) {
    std::string input(reinterpret_cast<const char*>(data), size);
    input.push_back('\0');
    parse_int(input.c_str());
    return 0;
}
