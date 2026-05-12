#include <cstdint>
#include <cstddef>
#include <vector>

#include "cJSON.h"

extern "C" int LLVMFuzzerTestOneInput(const uint8_t* data, size_t size) {
    std::vector<char> input(data, data + size);
    input.push_back('\0');

    cJSON* root = cJSON_ParseWithLength(input.data(), size);
    if (root != nullptr) {
        cJSON_Delete(root);
    }

    return 0;
}
