#include <iostream>
#include <string>
#include <vector>
#include <cstring>
#include <map>

// Optimized AELIS Audio Store
struct Buffer {
    char* data;
    size_t size;
};

std::map<std::string, Buffer> cache;

extern "C" {
    void store_audio(const char* key, const char* buffer, int length) {
        std::string skey(key);
        if (cache.count(skey)) {
            delete[] cache[skey].data;
        }
        char* new_data = new char[length];
        std::memcpy(new_data, buffer, length);
        cache[skey] = { new_data, (size_t)length };
    }

    int has_audio(const char* key) {
        return cache.count(std::string(key)) > 0;
    }

    int get_audio_size(const char* key) {
        std::string skey(key);
        if (cache.count(skey)) return (int)cache[skey].size;
        return 0;
    }

    void get_audio_data(const char* key, char* output) {
        std::string skey(key);
        if (cache.count(skey)) {
            std::memcpy(output, cache[skey].data, cache[skey].size);
        }
    }

    const char* analyze_prosody(const char* text) {
        std::string s(text);
        if (s.find('?') != std::string::npos) return "pitch=+10Hz rate=+0%";
        if (s.find('!') != std::string::npos) return "pitch=+5Hz rate=+5%";
        return "pitch=+0Hz rate=+0%";
    }
}
