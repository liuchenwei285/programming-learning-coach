#include <stdio.h>

int main(void)
{
    int c;

    if (scanf("%d", &c) != 1) {
        return 1;
    }

    double f = c / 5.0 * 9 + 32;

    printf("%.2f\n", f);
    return 0;
}
