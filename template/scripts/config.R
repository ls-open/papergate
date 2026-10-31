# 项目分析管线配置 (R 版本)
# 所有 R 脚本通过 source("scripts/config.R") 使用
# 项目根由本文件自身位置推导（与 Python 端 Path(__file__) 一致），
# 任意目录下 Rscript 均可运行，不依赖 getwd()。

SCRIPT_PATH <- local({
    a <- commandArgs(trailingOnly = FALSE)
    f <- sub("^--file=", "", a[grep("^--file=", a)])
    if (length(f) > 0) normalizePath(f[1]) else NA_character_
})
# source() 场景：从调用帧取 ofile；交互式 source 才回退 getwd()
if (is.na(SCRIPT_PATH)) {
    for (fr in rev(sys.frames())) {
        of <- fr$ofile
        if (!is.null(of)) { SCRIPT_PATH <- normalizePath(of); break }
    }
}
if (is.na(SCRIPT_PATH)) SCRIPT_PATH <- getwd()

# ── 确定性契约：锁定 LC_COLLATE ───────────────────────────────────────
# locale 是隐式进程级设置：输入一字未改，排序规则变了结果就变，文件哈希发现不了。
# 典型受害者是 stm::textProcessor()——它用 sort() 建词表，locale 一变词表**顺序**
# 随之改变（长度不变），而谱初始化按序送 SVD：列序改变 → 锚词改变 → 主题结构漂移。
# 只锁 LC_COLLATE：LC_NUMERIC 非 C 会破坏数值解析，且 R 默认已是 C。
PAPERGATE_LC_COLLATE <- Sys.getenv("PAPERGATE_LC_COLLATE", unset = "C.UTF-8")

.pin_locale <- function(loc = PAPERGATE_LC_COLLATE) {
    if (is.na(suppressWarnings(Sys.setlocale("LC_COLLATE", loc)))) {
        stop(sprintf(paste0("无法锁定 LC_COLLATE=%s。排序相关的分析结果依赖这一项；",
                            "修复运行环境，或用 PAPERGATE_LC_COLLATE 指定本机支持的",
                            "确定性 locale。"), loc), call. = FALSE)
    }
    cat(sprintf("[config.R] LC_COLLATE 已锁定为 %s\n", Sys.getlocale("LC_COLLATE")))
    invisible(TRUE)
}
.pin_locale()

# 向上找 .papergate_config 标记定位项目根（兼容 scripts/ 直下与 scripts/exploration/ 子目录脚本）
find_root <- function(p) {
    d <- dirname(p)
    while (d != "/" && !file.exists(file.path(d, ".papergate_config"))) d <- dirname(d)
    d
}
root <- find_root(SCRIPT_PATH)
PROJECT_ROOT <- if (root == "/") dirname(dirname(SCRIPT_PATH)) else root
DATA_DIR <- file.path(PROJECT_ROOT, "data")
PROCESSED_DIR <- file.path(PROJECT_ROOT, "processed")
RESULTS_DIR <- file.path(PROJECT_ROOT, "results")
DIAGNOSTICS_DIR <- file.path(RESULTS_DIR, "diagnostics")
FIGURES_DIR <- file.path(RESULTS_DIR, "figures")
