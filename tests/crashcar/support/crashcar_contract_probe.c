#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <cohfar/crashcar_singlefar.h>
#include <postcohtable.h>

static void print_boolean(gboolean value) {
    printf("%s", value ? "true" : "false");
}

static void print_real4_array(const REAL4 values[MAX_NIFO]) {
    printf("[");
    for (int ifo_id = 0; ifo_id < MAX_NIFO; ++ifo_id) {
        if (ifo_id != 0) printf(",");
        printf("%.9g", (double)values[ifo_id]);
    }
    printf("]");
}

static void initialize_row(PostcohInspiralTable *row) {
    memset(row, 0, sizeof(*row));
    row->H1_LLR = 100.25;
    row->L1_LLR = 101.25;
    for (int ifo_id = 0; ifo_id < MAX_NIFO; ++ifo_id) {
        row->far_sngl[ifo_id] = (REAL4)(120.25 + ifo_id);
        row->far_1w_sngl[ifo_id] = (REAL4)(130.25 + ifo_id);
        row->far_1d_sngl[ifo_id] = (REAL4)(140.25 + ifo_id);
        row->far_2h_sngl[ifo_id] = (REAL4)(150.25 + ifo_id);
    }
}

static void print_row_snapshot(const PostcohInspiralTable *row) {
    printf("{\"H1_LLR\":%.17g", (double)row->H1_LLR);
    printf(",\"L1_LLR\":%.17g", (double)row->L1_LLR);
    printf(",\"far_sngl\":");
    print_real4_array(row->far_sngl);
    printf(",\"far_1w_sngl\":");
    print_real4_array(row->far_1w_sngl);
    printf(",\"far_1d_sngl\":");
    print_real4_array(row->far_1d_sngl);
    printf(",\"far_2h_sngl\":");
    print_real4_array(row->far_2h_sngl);
    printf("}");
}

static const char *route_name(CrashcarSingleFinalRoute route) {
    switch (route) {
    case CRASHCAR_SINGLE_FINAL_ROUTE_INVALID:
        return "INVALID";
    case CRASHCAR_SINGLE_FINAL_ROUTE_H1:
        return "H1";
    case CRASHCAR_SINGLE_FINAL_ROUTE_L1:
        return "L1";
    case CRASHCAR_SINGLE_FINAL_ROUTE_MULTI:
        return "MULTI";
    case CRASHCAR_SINGLE_FINAL_ROUTE_V1_ONLY:
        return "V1_ONLY";
    default:
        return "UNKNOWN";
    }
}

static void print_route_case(const char *label, const char *ifos) {
    const CrashcarSingleFinalRoute route =
      crashcar_singlefar_final_route_from_ifos(ifos);
    printf("\"%s\":{\"ifos\":\"%s\",\"route_id\":%d,"
           "\"route\":\"%s\",\"assigns_ifo\":[",
           label, ifos, (int)route, route_name(route));
    for (int ifo_id = 0; ifo_id < MAX_NIFO; ++ifo_id) {
        if (ifo_id != 0) printf(",");
        print_boolean(crashcar_singlefar_route_assigns_ifo(route, ifo_id));
    }
    printf("],\"assigns_invalid_low\":");
    print_boolean(crashcar_singlefar_route_assigns_ifo(route, -1));
    printf(",\"assigns_invalid_high\":");
    print_boolean(crashcar_singlefar_route_assigns_ifo(route, MAX_NIFO));
    printf("}");
}

static void print_ifo_cases(void) {
    static const char *values[] = {
        "H1L1", "H1", "L1", "H1H1", "H1L1H1", "H1junk",
        "H1V1", "H1K1", "V1", "K1", "L1H1", "H1L1V1",
    };
    printf("\"ifo_validator\":{");
    for (size_t index = 0; index < sizeof(values) / sizeof(values[0]);
         ++index) {
        if (index != 0) printf(",");
        printf("\"%s\":", values[index]);
        print_boolean(crashcar_singlefar_ifos_valid(values[index]));
    }
    printf("}");
}

static void print_default_contracts(void) {
    PostcohInspiralTable before;
    PostcohInspiralTable after;

    initialize_row(&before);
    after = before;
    crashcar_singlefar_prepare_row_llrs(&after);
    crashcar_singlefar_prepare_row_llrs(NULL);

    printf("{\"schema_version\":4,\"max_nifo\":%d,\"row_prepare\":{",
           MAX_NIFO);
    printf("\"prepare_all_llrs\":{\"before\":");
    print_row_snapshot(&before);
    printf(",\"after\":");
    print_row_snapshot(&after);
    printf("},\"null_table_completed\":true},\"routes\":{");
    print_route_case("H", "H1");
    printf(",");
    print_route_case("Hv", "H1V1");
    printf(",");
    print_route_case("L", "L1");
    printf(",");
    print_route_case("Lv", "L1V1");
    printf(",");
    print_route_case("HL", "H1L1");
    printf(",");
    print_route_case("HLV", "H1L1V1");
    printf(",");
    print_route_case("V", "V1");
    printf(",");
    print_route_case("invalid", "L1H1");
    printf("},\"invalid_route_assigns_ifo\":[");
    for (int ifo_id = 0; ifo_id < MAX_NIFO; ++ifo_id) {
        if (ifo_id != 0) printf(",");
        print_boolean(crashcar_singlefar_route_assigns_ifo(
          (CrashcarSingleFinalRoute)99, ifo_id));
    }
    printf("],");
    print_ifo_cases();
    printf("}\n");
}


static int validate_template_map(const char *path,
                                 const char *expected_sha256) {
    static const char *header =
      "ifo_id,bankid,tmplt_idx,a_eff,dof,ifo,source_class";
    static const unsigned int rows_per_ifo = 384u * 1000u;
    static const unsigned int expected_rows = 2u * 384u * 1000u;
    FILE *input = fopen(path, "rb");
    if (!input) return 10;
    GChecksum *checksum = g_checksum_new(G_CHECKSUM_SHA256);
    if (!checksum) {
        fclose(input);
        return 11;
    }
    unsigned char block[65536];
    size_t got = 0;
    while ((got = fread(block, 1, sizeof(block), input)) > 0) {
        g_checksum_update(checksum, block, got);
    }
    if (ferror(input) || fseek(input, 0, SEEK_SET) != 0) {
        g_checksum_free(checksum);
        fclose(input);
        return 12;
    }
    const char *actual_sha256 = g_checksum_get_string(checksum);
    if (!actual_sha256 || strcmp(actual_sha256, expected_sha256) != 0) {
        g_checksum_free(checksum);
        fclose(input);
        return 13;
    }

    char line[512];
    unsigned int line_number = 0;
    unsigned int loaded = 0;
    while (fgets(line, sizeof(line), input)) {
        ++line_number;
        const size_t length = strlen(line);
        if (length == 0 || line[length - 1] != '\n' ||
            strchr(line, '\r') != NULL) {
            g_checksum_free(checksum);
            fclose(input);
            return 14;
        }
        line[length - 1] = '\0';
        if (line_number == 1) {
            if (strcmp(line, header) != 0) {
                g_checksum_free(checksum);
                fclose(input);
                return 15;
            }
            continue;
        }
        int ifo_id = -1;
        int bankid = -1;
        int tmplt_idx = -1;
        double a_eff = 0.0;
        double dof = 0.0;
        if (loaded >= expected_rows ||
            !crashcar_singlefar_parse_template_shape_row(
              line, &ifo_id, &bankid, &tmplt_idx, &a_eff, &dof)) {
            g_checksum_free(checksum);
            fclose(input);
            return 16;
        }
        const unsigned int expected_ifo = loaded / rows_per_ifo;
        const unsigned int within_ifo = loaded % rows_per_ifo;
        const unsigned int expected_bank = within_ifo / 1000u;
        const unsigned int expected_tmplt = within_ifo % 1000u;
        const double expected_dof = expected_bank <= 99u ? 120.0 : 600.0;
        if ((unsigned int)ifo_id != expected_ifo ||
            (unsigned int)bankid != expected_bank ||
            (unsigned int)tmplt_idx != expected_tmplt ||
            !(a_eff > 0.0) || !isfinite(a_eff) || dof != expected_dof) {
            g_checksum_free(checksum);
            fclose(input);
            return 17;
        }
        ++loaded;
    }
    const int read_failed = ferror(input);
    const int close_failed = fclose(input) != 0;
    if (read_failed || close_failed || line_number != expected_rows + 1u ||
        loaded != expected_rows) {
        g_checksum_free(checksum);
        return 18;
    }
    printf("{\"sha256\":\"%s\",\"line_count\":%u,"
           "\"row_count\":%u,\"ifos\":2,\"banks\":384,"
           "\"templates_per_bank\":1000}\n",
           actual_sha256, line_number, loaded);
    g_checksum_free(checksum);
    return 0;
}

static int parse_template_row(const char *line) {
    int ifo_id = -1;
    int bankid = -1;
    int tmplt_idx = -1;
    double a_eff = 0.0;
    double dof = 0.0;
    if (!crashcar_singlefar_parse_template_shape_row(
          line, &ifo_id, &bankid, &tmplt_idx, &a_eff, &dof)) {
        return 4;
    }
    printf("{\"ifo_id\":%d,\"ifo\":\"%s\",\"bankid\":%d,"
           "\"tmplt_idx\":%d,\"autocorr_power\":%.17g,"
           "\"dof\":%.17g,\"source_class\":\"%s\"}\n",
           ifo_id, ifo_id == 0 ? "H1" : "L1", bankid, tmplt_idx,
           a_eff, dof, bankid <= 99 ? "BNS" : "NSBH");
    return 0;
}

int main(int argc, char **argv) {
    if (argc == 4 && strcmp(argv[1], "--validate-template-map") == 0) {
        return validate_template_map(argv[2], argv[3]);
    }
    if (argc == 3 && strcmp(argv[1], "--parse-template-row") == 0) {
        return parse_template_row(argv[2]);
    }
    if (argc == 3 && strcmp(argv[1], "--validate-ifos") == 0) {
        return crashcar_singlefar_ifos_valid(argv[2]) ? 0 : 5;
    }
    if (argc != 1) {
        fprintf(stderr, "usage: %s [--parse-template-row ROW | "
                        "--validate-ifos IFOS | --validate-template-map "
                        "PATH SHA256]\n", argv[0]);
        return 64;
    }
    print_default_contracts();
    return 0;
}
