#include "tiny.h"

#include <cstdlib>
#include <cstring>

int parse_int(const char* s) {
    if (s == nullptr) {
        return 0;
    }

    if (std::strcmp(s, "CRASH") == 0) {
        std::abort();
    }

    return std::atoi(s);
}
