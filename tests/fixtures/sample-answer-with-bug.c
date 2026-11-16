#include <stdio.h>

int main(void)
{
    char ch;
    int kind;
    int value = -1;
    char conv;

    if (scanf(" %c", &ch) != 1) {
        return 1;
    }

    conv = ch;
    if (ch >= 'A' && ch <= 'Z') {
        conv = ch + 32;
        kind = 1;
    } else if (ch >= 'a' && ch <= 'z') {
        conv = ch - 32;
        kind = 2;
    } else if (ch >= '1' && ch <= '9') {
        value = ch - '0';
        kind = 3;
    } else {
        kind = 4;
    }

    printf("字符:%c\n", ch);
    printf("ASCII:%d\n", ch);
    printf("转换后:%c\n", conv);
    printf("数值:%d\n", value);

    switch (kind) {
        case 1: printf("类型:大写字母\n"); break;
        case 2: printf("类型:小写字母\n"); break;
        case 3: printf("类型:数字字符\n"); break;
        default: printf("类型:其他\n"); break;
    }
    return 0;
}
