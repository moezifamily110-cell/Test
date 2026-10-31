// SICAR Wiki Crawler v15 - Complete
//
// اصول:
//   - هیچ مقداری حدس زده نمی‌شود؛ هر فیلد یا مستقیم از منبع خوانده می‌شود یا با قاعده‌ای
//     قطعی و مستند از همان منبع به دست می‌آید، وگرنه خالی می‌ماند.
//   - فایل‌های JSON/PDF سایت بایت‌به‌بایت و بدون تغییر ذخیره می‌شوند (نسخه فعلی + نسخه‌های قدیمی).
//   - ارتباط خودرو↔ECU فقط از متن صریح سایت گرفته می‌شود.
//   - داده منابع مختلف ادغام نمی‌شوند؛ نتیجه مقایسه در گزارش‌های جداگانه ثبت می‌شود.
//   - Resume واقعی: هر صفحه/فایل پس از کامل شدن در فایل‌های JSONL ثبت می‌شود.
package main

import (
	"bufio"
	"bytes"
	"crypto/sha1"
	"crypto/sha256"
	"encoding/csv"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/url"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/PuerkitoBio/goquery"
)

const (
	Version   = "15.0"
	UserAgent = "SICAR-ECU-Reference-Crawler/15.0 (engineering research)"
)

// ---------------------------------------------------------------- config
type Config struct {
	BaseURL      string
	StartURL     string
	OutDir       string
	Workers      int
	Rate         time.Duration
	Timeout      time.Duration
	Retries      int
	History      bool
	GitHub       bool
	GitHubRaw    string
	Fresh        bool
	MaxPages     int
	AllowedHosts []string
}

func parseFlags() Config {
	c := Config{}
	flag.StringVar(&c.BaseURL, "base", "https://ecu.sisoog.com", "آدرس پایه سایت")
	flag.StringVar(&c.StartURL, "start", "https://ecu.sisoog.com/wiki/%D8%A7%D9%88%D9%84%DB%8C%D9%86_%D9%85%D8%B1%D8%AC%D8%B9_%D9%85%D8%AA%D9%86_%D8%A8%D8%A7%D9%88_ECU_%D8%AF%D8%B1_%D8%A7%DB%8C%D8%B1%D8%A7%D9%86", "صفحه شروع")
	flag.StringVar(&c.OutDir, "out", "output", "پوشه خروجی")
	flag.IntVar(&c.Workers, "workers", 6, "تعداد worker")
	flag.DurationVar(&c.Rate, "rate", 350*time.Millisecond, "حداقل فاصله بین دو درخواست به سایت (سراسری)")
	flag.DurationVar(&c.Timeout, "timeout", 30*time.Second, "timeout هر درخواست")
	flag.IntVar(&c.Retries, "retries", 3, "تعداد تلاش برای خطای شبکه/429/5xx")
	flag.BoolVar(&c.History, "history", true, "دانلود نسخه‌های قدیمی فایل‌ها (Version History)")
	flag.BoolVar(&c.GitHub, "github", true, "مقایسه فایل‌های JSON با مخزن Sisoog/sicar-diag")
	flag.StringVar(&c.GitHubRaw, "github-raw", "https://raw.githubusercontent.com/Sisoog/sicar-diag/main/jsons/", "پیشوند فایل‌های JSON در GitHub")
	flag.BoolVar(&c.Fresh, "fresh", false, "نادیده گرفتن state قبلی و شروع از صفر")
	flag.IntVar(&c.MaxPages, "max-pages", 0, "حداکثر صفحه (0 = نامحدود؛ برای تست)")
	flag.Parse()
	u, _ := url.Parse(c.BaseURL)
	c.AllowedHosts = []string{u.Host}
	return c
}

// ---------------------------------------------------------------- data types
type ECURecord struct {
	RecordID           string `json:"record_id"`
	RecordType         string `json:"record_type"`
	VehicleBrand       string `json:"vehicle_brand"`
	VehicleModel       string `json:"vehicle_model"`
	VehicleType        string `json:"vehicle_type"`
	VehicleYear        string `json:"vehicle_year"`
	Manufacturer       string `json:"manufacturer"`
	ECUManufacturer    string `json:"ecu_manufacturer"`
	ECUModel           string `json:"ecu_model"`
	Protocol           string `json:"protocol"`
	BaudRate           string `json:"baud_rate"`
	InitCommands       string `json:"init_commands"`
	WakeupCommands     string `json:"wakeup_commands"`
	ReinitCommands     string `json:"reinit_commands"`
	ECUAddress         string `json:"ecu_address"`
	TimeoutMs          string `json:"timeout_ms"`
	CommandType        string `json:"command_type"`
	ParameterName      string `json:"parameter_name"`
	RequestCommand     string `json:"request_command"`
	ResponseHeader     string `json:"response_header"`
	Formula            string `json:"formula"`
	FormulaClass       string `json:"formula_class"`
	ByteAddress        string `json:"byte_address"`
	BitAddress         string `json:"bit_address"`
	Coefficient        string `json:"coefficient"`
	Offset             string `json:"offset"`
	Unit               string `json:"unit"`
	MinValue           string `json:"min_value"`
	MaxValue           string `json:"max_value"`
	IsDTC              bool   `json:"is_dtc"`
	DTCCode            string `json:"dtc_code"`
	DTCDescription     string `json:"dtc_description"`
	DTCDescriptionEN   string `json:"dtc_description_en"`
	VehicleLabelRaw    string `json:"vehicle_label_raw"`
	ApplicableVehicles string `json:"applicable_vehicles"`
	EcuPageURL         string `json:"ecu_page_url"`
	FileName           string `json:"file_name"`
	FileSHA256         string `json:"file_sha256"`
	ConnectionSpecs    string `json:"connection_specs"`
	SiteJSONCheck      string `json:"site_json_check"`
	SourceURL          string `json:"source_url"`
	PageTitle          string `json:"page_title"`
	SourceKind         string `json:"source_kind"`
	RetrievedAt        string `json:"retrieved_at"`
	ConfidenceScore    int    `json:"confidence_score"`
	ConfidencePct      int    `json:"confidence_pct"`
	SourceTier         string `json:"source_tier"`
	VerificationStatus string `json:"verification_status"`
	Notes              string `json:"notes"`
}

type FileLink struct {
	PageURL   string `json:"page_url"`
	Name      string `json:"name"`
	Ext       string `json:"ext"`
	LinkText  string `json:"link_text"`
	Vehicle   string `json:"vehicle"`
	Company   string `json:"company"`
	ECU       string `json:"ecu"`
	FromPage  string `json:"from_page"`
	FromTitle string `json:"from_title"`
}

type VehicleECU struct {
	VehiclePage  string `json:"vehicle_page"`
	VehicleTitle string `json:"vehicle_title"`
	Trim         string `json:"trim"`
	ECUPageURL   string `json:"ecu_page_url"`
	ECULinkText  string `json:"ecu_link_text"`
}

type Section struct {
	Manufacturer     string   `json:"manufacturer"`
	ManufacturerPage string   `json:"manufacturer_page"`
	Vehicles         []string `json:"vehicles"`
	VehiclePages     []string `json:"vehicle_pages"`
}

type GenericTable struct {
	TableIndex int        `json:"table_index"`
	Header     []string   `json:"header"`
	Rows       [][]string `json:"rows"`
}

type PageResult struct {
	URL           string         `json:"url"`
	Title         string         `json:"title"`
	FetchedAt     string         `json:"fetched_at"`
	Links         []string       `json:"links"`
	FileLinks     []FileLink     `json:"file_links"`
	ExternalFiles []string       `json:"external_files"`
	ExternalLinks []FileLink     `json:"external_links,omitempty"`
	VehicleMap    []VehicleECU   `json:"vehicle_map,omitempty"`
	TableLinks    []VehicleECU   `json:"table_links,omitempty"`
	Sections      []Section      `json:"sections,omitempty"`
	TableKinds    []string       `json:"table_kinds"`
	GenericTables []GenericTable `json:"generic_tables,omitempty"`
	SkippedRows   [][]string     `json:"skipped_rows,omitempty"`
	RecordCount   int            `json:"record_count"`
}

type FileVersion struct {
	URL          string `json:"url"`
	Timestamp    string `json:"timestamp"`
	DateText     string `json:"date_text"`
	Comment      string `json:"comment"`
	LocalPath    string `json:"local_path"`
	SHA256       string `json:"sha256"`
	Size         int64  `json:"size"`
	LastModified string `json:"last_modified"`
	ContentType  string `json:"content_type"`
	ValidJSON    *bool  `json:"valid_json,omitempty"`
	Error        string `json:"error,omitempty"`
}

type FileResult struct {
	Kind      string        `json:"kind"`
	PageURL   string        `json:"page_url"`
	Name      string        `json:"name"`
	Ext       string        `json:"ext"`
	Current   FileVersion   `json:"current"`
	History   []FileVersion `json:"history"`
	FetchedAt string        `json:"fetched_at"`
}

type ErrorEntry struct {
	URL   string `json:"url"`
	Stage string `json:"stage"`
	Error string `json:"error"`
	At    string `json:"at"`
}

type GitHubCheck struct {
	Name      string   `json:"name"`
	URL       string   `json:"url"`
	Status    string   `json:"status"`
	SiteSHA   string   `json:"site_sha256"`
	GitHubSHA string   `json:"github_sha256"`
	LocalPath string   `json:"local_path"`
	Diff      []string `json:"diff,omitempty"`
	CheckedAt string   `json:"checked_at"`
	Error     string   `json:"error,omitempty"`
}

type Task struct {
	Kind string
	URL  string
}

// ---------------------------------------------------------------- normalization
var faDigits = strings.NewReplacer(
	"۰", "0", "۱", "1", "۲", "2", "۳", "3", "۴", "4", "۵", "5", "۶", "6", "۷", "7", "۸", "8", "۹", "9",
	"٠", "0", "١", "1", "٢", "2", "٣", "3", "٤", "4", "٥", "5", "٦", "6", "٧", "7", "٨", "8", "٩", "9",
	"ي", "ی", "ى", "ی", "ك", "ک", "ة", "ه", "ـ", "", "\u200c", " ", "\u200f", "", "\u200e", "", "\u00a0", " ",
)

var wsRe = regexp.MustCompile(`\s+`)

func norm(s string) string {
	return strings.ToLower(strings.TrimSpace(wsRe.ReplaceAllString(faDigits.Replace(s), " ")))
}

func normCmd(s string) string {
	return strings.ToUpper(strings.ReplaceAll(wsRe.ReplaceAllString(s, ""), " ", ""))
}

func normFormula(s string) string { return wsRe.ReplaceAllString(faDigits.Replace(s), "") }

var (
	idMulRe = regexp.MustCompile(`[*/]1(?:\.0+)?([^\d.]|$)`)
	idAddRe = regexp.MustCompile(`[+-]0(?:\.0+)?([^\d.]|$)`)
)

func identityStrip(f string) string {
	s := normFormula(f)
	for {
		t := idAddRe.ReplaceAllString(idMulRe.ReplaceAllString(s, "$1"), "$1")
		if t == s {
			return s
		}
		s = t
	}
}

func clean(s string) string {
	return strings.TrimSpace(wsRe.ReplaceAllString(strings.ReplaceAll(s, "\u200f", ""), " "))
}

func shortID(parts ...string) string {
	h := sha1.Sum([]byte(strings.Join(parts, "\x1f")))
	return hex.EncodeToString(h[:8])
}

func nowISO() string { return time.Now().UTC().Format(time.RFC3339) }

// ---------------------------------------------------------------- formula analysis
var (
	reByteIdx    = regexp.MustCompile(`B\[(\d+)(?:~(\d+))?\]`)
	reBitFlag    = regexp.MustCompile(`B\[(\d+)\]\s*&\s*\(\s*1\s*<<\s*(\d+)\s*\)`)
	reBitMask    = regexp.MustCompile(`B\[(\d+)\]\s*&\s*(0[xX][0-9A-Fa-f]+|\d+)\b`)
	reShift      = regexp.MustCompile(`B\[(\d+)\]\s*>>\s*(\d+)`)
	reLinear     = regexp.MustCompile(`^\(?\s*B\[(\d+)\]\s*(?:([*/])\s*(-?\d+(?:\.\d+)?))?\s*\)?\s*(?:([+-])\s*(\d+(?:\.\d+)?))?$`)
	reOnlyBitFlg = regexp.MustCompile(`^\(?\s*B\[(\d+)\]\s*&\s*\(\s*1\s*<<\s*(\d+)\s*\)\s*\)?$`)
	reRangeOnly  = regexp.MustCompile(`^B\[(\d+)~(\d+)\]$`)
)

func fmtFloat(f float64) string { return strconv.FormatFloat(f, 'g', 12, 64) }

func analyzeFormula(f string) (byteAddr, bitAddr, coeff, offset, class string) {
	f = strings.TrimSpace(f)
	if f == "" {
		return "", "", "", "", ""
	}
	seen := map[string]bool{}
	var bytesL []string
	for _, m := range reByteIdx.FindAllStringSubmatch(f, -1) {
		k := m[1]
		if m[2] != "" {
			k = m[1] + "~" + m[2]
		}
		if !seen[k] {
			seen[k] = true
			bytesL = append(bytesL, k)
		}
	}
	byteAddr = strings.Join(bytesL, ",")
	var bits []string
	for _, m := range reBitFlag.FindAllStringSubmatch(f, -1) {
		bits = append(bits, m[1]+"."+m[2])
	}
	for _, m := range reBitMask.FindAllStringSubmatch(f, -1) {
		v, err := strconv.ParseInt(strings.TrimPrefix(strings.ToLower(m[2]), "0x"), map[bool]int{true: 16, false: 10}[strings.HasPrefix(strings.ToLower(m[2]), "0x")], 64)
		if err == nil && v > 0 && v&(v-1) == 0 {
			bits = append(bits, m[1]+"."+strconv.Itoa(int(math.Log2(float64(v)))))
		}
	}
	for _, m := range reShift.FindAllStringSubmatch(f, -1) {
		bits = append(bits, m[1]+">>"+m[2])
	}
	bitAddr = strings.Join(bits, ",")
	switch {
	case strings.Contains(f, "PIDOF"):
		class = "pid_reference"
	case strings.Contains(f, "?"):
		class = "conditional"
	case reOnlyBitFlg.MatchString(f):
		class = "bit_flag"
	case reRangeOnly.MatchString(f):
		class = "byte_range"
	case strings.Contains(f, "INFO{") || (strings.Contains(f, "O[") && strings.Contains(f, "C[")):
		class = "dtc_decode"
	default:
		if m := reLinear.FindStringSubmatch(f); m != nil {
			class = "linear_single_byte"
			c := 1.0
			if m[3] != "" {
				v, _ := strconv.ParseFloat(m[3], 64)
				if m[2] == "/" {
					if v != 0 {
						c = 1 / v
					} else {
						class = "expression"
					}
				} else {
					c = v
				}
			}
			o := 0.0
			if m[5] != "" {
				o, _ = strconv.ParseFloat(m[5], 64)
				if m[4] == "-" {
					o = -o
				}
			}
			if class == "linear_single_byte" {
				coeff, offset = fmtFloat(c), fmtFloat(o)
			}
		} else if len(bytesL) > 1 {
			class = "multi_byte"
		} else if len(bytesL) == 0 {
			class = "text_or_constant"
		} else {
			class = "expression"
		}
	}
	return
}

// ---------------------------------------------------------------- protocol tables
var elmProtocols = map[string][3]string{
	"0":  {"Automatic", "", ""},
	"1":  {"SAE J1850 PWM", "41600", "J1850"},
	"2":  {"SAE J1850 VPW", "10400", "J1850"},
	"3":  {"ISO 9141-2 (5 baud init)", "10400", "K-Line"},
	"4":  {"ISO 14230-4 KWP (5 baud init)", "10400", "K-Line"},
	"5":  {"ISO 14230-4 KWP (fast init)", "10400", "K-Line"},
	"6":  {"ISO 15765-4 CAN (11 bit ID)", "500000", "CAN"},
	"7":  {"ISO 15765-4 CAN (29 bit ID)", "500000", "CAN"},
	"8":  {"ISO 15765-4 CAN (11 bit ID)", "250000", "CAN"},
	"9":  {"ISO 15765-4 CAN (29 bit ID)", "250000", "CAN"},
	"10": {"SAE J1939 CAN (29 bit ID)", "250000", "CAN"},
	"A":  {"SAE J1939 CAN (29 bit ID)", "250000", "CAN"},
}

var cmdTypeNames = map[string]string{
	"0": "cmd_noCmd", "1": "cmd_InitCommunication", "2": "cmd_Param_2", "3": "cmd_Operation", "4": "cmd_Trouble",
	"5": "cmd_ClearTrouble", "6": "cmd_ConnectToDevice_ELM", "7": "cmd_ConnectToDevice_ASAN", "8": "cmd_CountTroubleCode_08",
	"9": "cmd_ReadChar_09", "10": "cmd_EndOperation_10", "11": "cmd_PramReplaceText_11", "12": "cmd_ReadHex_12",
	"13": "cmd_AllDecimal_13", "14": "cmd_GetValue_Configuration_14", "15": "cmd_SetValue_Configuration_15",
	"16": "cmd_PendingTrouble_16", "17": "cmd_GroupLoop_17", "18": "cmd_ReInit", "19": "cmd_ReadFreezeFrame_19",
	"20": "cmd_Stop_Communication_20", "21": "cmd_SingleLoop_21", "22": "cmd_TextStr", "23": "cmd_wakeup",
	"24": "cmd_Trouble_No_OBD2_CAN", "25": "cmd_ReInit_Communication", "26": "cmd_Trouble_NoObd2",
	"27": "cmd_LoopUnlimited_27", "28": "cmd_SingleDecimal_28", "29": "cmd_MessageBox_29", "30": "cmd_Trouble_ReverseCode",
	"31": "cmd_Trouble_NoOby2_NoFaultCode", "32": "cmd_AvgParam", "33": "cmd_ReadAlphabetWithIndex_33",
	"34": "cmd_LoopCmdToExit_34", "35": "cmd_inputBox_35", "36": "cmd_Opr_IgnoreResp_36", "37": "cmd_CloseSession_37",
	"38": "cmd_findFirstValidResultInGroup", "39": "cmd_Opr_RespIsImportant_39", "40": "cmd_MapTable",
	"41": "cmd_SplitValueToCommands", "42": "cmd_Condition_42", "43": "cmd_SubGroupInOperation", "44": "cmd_BatchCommand",
}

// ---------------------------------------------------------------- crawler
type Crawler struct {
	vehByECUPage   map[string][]VehicleECU
	makerByVehPage map[string]string
	cfg            Config
	client         *http.Client
	limiter        *time.Ticker
	ghLim          *time.Ticker
	robots         []string
	robotsStatus   string

	queue   chan Task
	pending sync.WaitGroup
	seen    sync.Map
	pages   int64

	mu        sync.Mutex
	pagesF    *os.File
	filesF    *os.File
	recordsF  *os.File
	errorsF   *os.File

	pageResults map[string]PageResult
	fileResults map[string]FileResult
	records     []ECURecord
	errors      []ErrorEntry

	vehiclesTree []Section
	siteVsJSON   []map[string]interface{}
	ghChecks     []GitHubCheck
	versionHist  []map[string]interface{}
}

func NewCrawler(cfg Config) *Crawler {
	return &Crawler{
		cfg:            cfg,
		vehByECUPage:   map[string][]VehicleECU{},
		makerByVehPage: map[string]string{},
		client: &http.Client{
			Timeout: cfg.Timeout,
			CheckRedirect: func(req *http.Request, via []*http.Request) error {
				if len(via) >= 10 {
					return errors.New("too many redirects")
				}
				return nil
			},
		},
		limiter:     time.NewTicker(cfg.Rate),
		ghLim:       time.NewTicker(1 * time.Second),
		queue:       make(chan Task, 1000),
		pageResults: map[string]PageResult{},
		fileResults: map[string]FileResult{},
	}
}

func (c *Crawler) fetchURL(rawURL string) ([]byte, string, error) {
	<-c.limiter.C
	u, err := url.Parse(rawURL)
	if err != nil {
		return nil, "", err
	}
	allowed := false
	for _, h := range c.cfg.AllowedHosts {
		if u.Host == h {
			allowed = true
			break
		}
	}
	if !allowed && !strings.Contains(u.Host, "github") && !strings.Contains(u.Host, "githubusercontent") {
		return nil, "", fmt.Errorf("host not allowed: %s", u.Host)
	}

	var lastErr error
	for attempt := 0; attempt < c.cfg.Retries; attempt++ {
		req, err := http.NewRequest("GET", rawURL, nil)
		if err != nil {
			lastErr = err
			continue
		}
		req.Header.Set("User-Agent", UserAgent)
		req.Header.Set("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
		req.Header.Set("Accept-Language", "fa,en;q=0.9")

		resp, err := c.client.Do(req)
		if err != nil {
			lastErr = err
			time.Sleep(time.Duration(attempt+1) * time.Second)
			continue
		}
		if resp.StatusCode == 429 || (resp.StatusCode >= 500 && resp.StatusCode < 600) {
			resp.Body.Close()
			lastErr = fmt.Errorf("HTTP %d", resp.StatusCode)
			time.Sleep(time.Duration(attempt+2) * time.Second)
			continue
		}
		if resp.StatusCode != 200 {
			resp.Body.Close()
			return nil, resp.Header.Get("Content-Type"), fmt.Errorf("HTTP %d", resp.StatusCode)
		}
		data, err := io.ReadAll(resp.Body)
		resp.Body.Close()
		if err != nil {
			lastErr = err
			continue
		}
		return data, resp.Header.Get("Content-Type"), nil
	}
	return nil, "", lastErr
}

func (c *Crawler) fetchBytes(rawURL string) ([]byte, string, error) {
	return c.fetchURL(rawURL)
}

func (c *Crawler) isDisallowed(u *url.URL) bool {
	p := u.Path
	for _, d := range c.robots {
		if strings.HasPrefix(p, d) {
			return true
		}
	}
	return false
}

func (c *Crawler) loadRobots() {
	u, _ := url.Parse(c.cfg.BaseURL + "/robots.txt")
	data, _, err := c.fetchURL(u.String())
	if err != nil {
		c.robotsStatus = fmt.Sprintf("unavailable: %v", err)
		return
	}
	var inUA bool
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(strings.ToLower(line), "user-agent:") {
			v := strings.TrimSpace(line[len("user-agent:"):])
			inUA = (v == "*")
			continue
		}
		if inUA && strings.HasPrefix(strings.ToLower(line), "disallow:") {
			v := strings.TrimSpace(line[len("disallow:"):])
			if v != "" {
				c.robots = append(c.robots, v)
			}
		}
	}
	c.robotsStatus = fmt.Sprintf("loaded (%d rules)", len(c.robots))
}

func (c *Crawler) alreadySeen(key string) bool {
	_, loaded := c.seen.LoadOrStore(key, true)
	return loaded
}

func (c *Crawler) enqueue(kind, urlStr string) {
	if c.cfg.MaxPages > 0 && atomic.LoadInt64(&c.pages) >= int64(c.cfg.MaxPages) {
		return
	}
	if c.alreadySeen(kind + "|" + urlStr) {
		return
	}
	u, err := url.Parse(urlStr)
	if err != nil {
		return
	}
	if c.isDisallowed(u) {
		return
	}
	c.pending.Add(1)
	select {
	case c.queue <- Task{Kind: kind, URL: urlStr}:
	default:
		c.pending.Done()
	}
}

func (c *Crawler) resolveURL(base *url.URL, href string) string {
	if href == "" || strings.HasPrefix(href, "#") {
		return ""
	}
	if strings.HasPrefix(href, "//") {
		return "https:" + href
	}
	if strings.HasPrefix(href, "http://") || strings.HasPrefix(href, "https://") {
		return href
	}
	u, err := base.Parse(href)
	if err != nil {
		return ""
	}
	u.Fragment = ""
	return u.String()
}

// parsePage - تجزیه یک صفحه ویکی
func (c *Crawler) parsePage(rawURL string, data []byte) PageResult {
	baseURL, _ := url.Parse(rawURL)
	doc, err := goquery.NewDocumentFromReader(bytes.NewReader(data))
	if err != nil {
		return PageResult{URL: rawURL, FetchedAt: nowISO()}
	}

	title := clean(doc.Find("title").First().Text())
	pr := PageResult{URL: rawURL, Title: title, FetchedAt: nowISO()}

	// استخراج تمام لینک‌های داخلی ویکی
	doc.Find("div#mw-content-text a").Each(func(i int, s *goquery.Selection) {
		href, _ := s.Attr("href")
		resolved := c.resolveURL(baseURL, href)
		if resolved == "" {
			return
		}
		u, err := url.Parse(resolved)
		if err != nil {
			return
		}
		if !c.isAllowedHost(u.Host) {
			return
		}
		if strings.Contains(u.Path, "/wiki/") && !strings.Contains(u.Path, ":") {
			pr.Links = append(pr.Links, resolved)
		} else if strings.Contains(u.Path, "/wiki/پرونده:") {
			name := path.Base(u.Path)
			ext := strings.ToLower(filepath.Ext(name))
			fl := FileLink{
				PageURL:   resolved,
				Name:      name,
				Ext:       strings.TrimPrefix(ext, "."),
				LinkText:  clean(s.Text()),
				FromPage:  rawURL,
				FromTitle: title,
			}
			pr.FileLinks = append(pr.FileLinks, fl)
		}
	})

	// تشخیص بخش‌های سازنده در صفحه اصلی
	doc.Find("div#mw-content-text h2, div#mw-content-text h3").Each(func(i int, s *goquery.Selection) {
		heading := clean(s.Text())
		if strings.Contains(heading, "خودرو") || strings.Contains(heading, "سازنده") ||
			strings.Contains(heading, "ایران‌خودرو") || strings.Contains(heading, "سایپا") {
			sec := Section{Manufacturer: heading}
			s.NextUntil("h2, h3").Find("a").Each(func(j int, a *goquery.Selection) {
				href, _ := a.Attr("href")
				r := c.resolveURL(baseURL, href)
				if r != "" && strings.Contains(r, "/wiki/") {
					sec.Vehicles = append(sec.Vehicles, clean(a.Text()))
					sec.VehiclePages = append(sec.VehiclePages, r)
				}
			})
			if len(sec.Vehicles) > 0 {
				pr.Sections = append(pr.Sections, sec)
			}
		}
	})

	// جداول فنی (wikitable)
	doc.Find("table.wikitable").Each(func(ti int, tbl *goquery.Selection) {
		gt := GenericTable{TableIndex: ti}
		tbl.Find("tr").Each(func(ri int, tr *goquery.Selection) {
			var row []string
			tr.Find("th, td").Each(func(ci int, cell *goquery.Selection) {
				row = append(row, clean(cell.Text()))
			})
			if ri == 0 {
				gt.Header = row
			} else {
				gt.Rows = append(gt.Rows, row)
			}
		})
		pr.GenericTables = append(pr.GenericTables, gt)

		// تشخیص نوع جدول از سرستون
		hdr := strings.Join(gt.Header, " ")
		switch {
		case strings.Contains(hdr, "مدل") && strings.Contains(hdr, "ECU"):
			pr.TableKinds = append(pr.TableKinds, "vehicle_ecu_map")
			// استخراج نگاشت خودرو↔ECU
			for _, row := range gt.Rows {
				if len(row) >= 2 {
					ve := VehicleECU{
						VehicleTitle: row[0],
						ECULinkText:  row[1],
					}
					// پیدا کردن لینک در سلول دوم
					tbl.Find("tr").Eq(ti + 1).Find("td").Eq(1).Find("a").Each(func(k int, a *goquery.Selection) {
						href, _ := a.Attr("href")
						ve.ECUPageURL = c.resolveURL(baseURL, href)
					})
					pr.TableLinks = append(pr.TableLinks, ve)
				}
			}
		case strings.Contains(hdr, "پارامتر") || strings.Contains(hdr, "فرمول") || strings.Contains(hdr, "دستور"):
			pr.TableKinds = append(pr.TableKinds, "parameter_table")
		case strings.Contains(hdr, "خطا") || strings.Contains(hdr, "DTC") || strings.Contains(hdr, "کد"):
			pr.TableKinds = append(pr.TableKinds, "dtc_table")
		default:
			pr.TableKinds = append(pr.TableKinds, "generic")
		}
	})

	return pr
}

func (c *Crawler) isAllowedHost(host string) bool {
	for _, h := range c.cfg.AllowedHosts {
		if host == h {
			return true
		}
	}
	return false
}

// استخراج رکوردها از فایل JSON سایت
func (c *Crawler) parseSicarJSON(data []byte, name, sha, pageURL, ecuPageURL string) []ECURecord {
	var root map[string]interface{}
	if err := json.Unmarshal(data, &root); err != nil {
		return nil
	}

	var records []ECURecord
	conn, _ := root["CONNECTION"].(map[string]interface{})
	specs, _ := conn["SPECS"].(map[string]interface{})

	initCmds, _ := conn["INITCMDLIST"].([]interface{})
	wakeCmds, _ := conn["WAKEUPCMDLIST"].([]interface{})
	reinitCmds, _ := conn["REINITCMDLIST"].([]interface{})

	baudtype, _ := specs["baudtype"]
	proto, baud := "", ""
	if bt := fmt.Sprintf("%v", baudtype); bt != "" {
		if p, ok := elmProtocols[bt]; ok {
			proto = fmt.Sprintf("%s: %s [ELM327 AT SP %s]", p[2], p[0], bt)
			baud = p[1]
		}
	}

	cmdList := func(items []interface{}) string {
		var parts []string
		for _, it := range items {
			if m, ok := it.(map[string]interface{}); ok {
				s := fmt.Sprintf("%v", m["cmdtext"])
				if cr, ok := m["cresp"]; ok && fmt.Sprintf("%v", cr) != "" {
					s += " → " + fmt.Sprintf("%v", cr)
				}
				if ct, ok := m["ctimewait"]; ok && fmt.Sprintf("%v", ct) != "" {
					s += " (" + fmt.Sprintf("%v", ct) + "ms)"
				}
				parts = append(parts, s)
			}
		}
		return strings.Join(parts, " ; ")
	}

	base := ECURecord{
		Protocol:        proto,
		BaudRate:        baud,
		InitCommands:    cmdList(initCmds),
		WakeupCommands:  cmdList(wakeCmds),
		ReinitCommands:  cmdList(reinitCmds),
		ECUAddress:      fmt.Sprintf("%v", specs["d"]),
		FileName:        name,
		FileSHA256:      sha,
		SourceURL:       pageURL,
		EcuPageURL:      ecuPageURL,
		SourceKind:      "site_json_file",
		SourceTier:      "Primary-SICAR-Site",
		ConfidenceScore: 9,
		ConfidencePct:   90,
		RetrievedAt:     nowISO(),
	}

	// INIT / WAKEUP / REINIT به‌عنوان رکورد مجزا
	for _, grp := range []struct {
		n string
		l []interface{}
		t string
	}{{"INIT", initCmds, "json_init"}, {"WAKEUP", wakeCmds, "json_wakeup"}, {"REINIT", reinitCmds, "json_reinit"}} {
		for i, it := range grp.l {
			if m, ok := it.(map[string]interface{}); ok {
				r := base
				r.RecordID = shortID(name, sha, grp.n, strconv.Itoa(i))
				r.RecordType = grp.t
				r.RequestCommand = fmt.Sprintf("%v", m["cmdtext"])
				r.ResponseHeader = fmt.Sprintf("%v", m["cresp"])
				r.TimeoutMs = fmt.Sprintf("%v", m["ctimewait"])
				r.VerificationStatus = "json_file_parsed"
				records = append(records, r)
			}
		}
	}

	// PARAMETER
	if params, ok := root["PARAMETER"].(map[string]interface{}); ok {
		keys := make([]string, 0, len(params))
		for k := range params {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, k := range keys {
			if items, ok := params[k].([]interface{}); ok {
				for i, it := range items {
					if m, ok := it.(map[string]interface{}); ok {
						r := base
						f := fmt.Sprintf("%v", m["cformula"])
						ba, bia, coeff, off, cls := analyzeFormula(f)
						ct := fmt.Sprintf("%v", m["ctype"])
						ctName := cmdTypeNames[ct]
						r.RecordID = shortID(name, sha, "PARAM", k, strconv.Itoa(i))
						r.RecordType = "json_parameter"
						r.CommandType = strings.TrimSpace(ct + " " + ctName)
						r.ParameterName = fmt.Sprintf("%v", m["cdesc"])
						r.RequestCommand = fmt.Sprintf("%v", m["cmdtext"])
						r.ResponseHeader = fmt.Sprintf("%v", m["cresp"])
						r.Formula = f
						r.FormulaClass = cls
						r.ByteAddress = ba
						r.BitAddress = bia
						r.Coefficient = coeff
						r.Offset = off
						r.Unit = fmt.Sprintf("%v", m["unit_sim"])
						r.MinValue = fmt.Sprintf("%v", m["cmin"])
						r.MaxValue = fmt.Sprintf("%v", m["cmax"])
						r.TimeoutMs = fmt.Sprintf("%v", m["ctimewait"])
						r.VerificationStatus = "json_file_parsed"
						r.Notes = fmt.Sprintf("cheader=%v; cunit=%v; decimalp=%v", m["cheader"], m["cunit"], m["decimalp"])
						records = append(records, r)
					}
				}
			}
		}
	}

	// DTC
	if dtcs, ok := root["DTC"].(map[string]interface{}); ok {
		keys := make([]string, 0, len(dtcs))
		for k := range dtcs {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, k := range keys {
			if items, ok := dtcs[k].([]interface{}); ok {
				for i, it := range items {
					if m, ok := it.(map[string]interface{}); ok {
						r := base
						f := fmt.Sprintf("%v", m["cformula"])
						r.RecordID = shortID(name, sha, "DTC", k, strconv.Itoa(i))
						r.RecordType = "json_dtc"
						r.IsDTC = true
						r.ParameterName = fmt.Sprintf("%v", m["cdesc"])
						r.RequestCommand = fmt.Sprintf("%v", m["cmdtext"])
						r.Formula = f
						r.DTCCode = k
						r.DTCDescription = fmt.Sprintf("%v", m["cdesc"])
						r.VerificationStatus = "json_file_parsed"
						records = append(records, r)
					}
				}
			}
		}
	}

	return records
}

// استخراج رکوردها از جداول صفحه ویکی
func (c *Crawler) extractTableRecords(pr PageResult) []ECURecord {
	var records []ECURecord
	for ti, gt := range pr.GenericTables {
		kind := "generic"
		if ti < len(pr.TableKinds) {
			kind = pr.TableKinds[ti]
		}
		if kind != "parameter_table" && kind != "dtc_table" {
			continue
		}

		// نگاشت ستون‌ها
		colMap := map[string]int{}
		for ci, h := range gt.Header {
			hl := norm(h)
			switch {
			case strings.Contains(hl, "پارامتر") || strings.Contains(hl, "نام") || strings.Contains(hl, "parameter") || strings.Contains(hl, "name"):
				colMap["parameter_name"] = ci
			case strings.Contains(hl, "دستور") || strings.Contains(hl, "فرمان") || strings.Contains(hl, "command") || strings.Contains(hl, "pid"):
				colMap["request_command"] = ci
			case strings.Contains(hl, "پاسخ") || strings.Contains(hl, "response"):
				colMap["response_header"] = ci
			case strings.Contains(hl, "فرمول") || strings.Contains(hl, "formula") || strings.Contains(hl, "equation"):
				colMap["formula"] = ci
			case strings.Contains(hl, "واحد") || strings.Contains(hl, "unit"):
				colMap["unit"] = ci
			case strings.Contains(hl, "حداقل") || strings.Contains(hl, "کمینه") || strings.Contains(hl, "min"):
				colMap["min_value"] = ci
			case strings.Contains(hl, "حداکثر") || strings.Contains(hl, "بیشینه") || strings.Contains(hl, "max"):
				colMap["max_value"] = ci
			case strings.Contains(hl, "کد خطا") || strings.Contains(hl, "dtc") || strings.Contains(hl, "کد"):
				colMap["dtc_code"] = ci
			case strings.Contains(hl, "توضیح") || strings.Contains(hl, "شرح") || strings.Contains(hl, "description"):
				colMap["dtc_description"] = ci
			}
		}

		if kind == "parameter_table" {
			if _, ok := colMap["parameter_name"]; !ok {
				continue
			}
		} else if kind == "dtc_table" {
			if _, ok := colMap["dtc_code"]; !ok {
				continue
			}
		}

		for ri, row := range gt.Rows {
			get := func(k string) string {
				if i, ok := colMap[k]; ok && i < len(row) {
					return clean(row[i])
				}
				return ""
			}

			r := ECURecord{
				RecordID:         shortID(pr.URL, strconv.Itoa(ti), strconv.Itoa(ri)),
				SourceURL:        pr.URL,
				PageTitle:        pr.Title,
				EcuPageURL:       pr.URL,
				SourceKind:       "site_page_table",
				SourceTier:       "Primary-SICAR-Site",
				ConfidenceScore:  8,
				ConfidencePct:    85,
				VerificationStatus: "site_table_extracted",
				RetrievedAt:      nowISO(),
			}

			if kind == "parameter_table" {
				r.RecordType = "pid_table"
				r.ParameterName = get("parameter_name")
				r.RequestCommand = get("request_command")
				r.ResponseHeader = get("response_header")
				f := get("formula")
				r.Formula = f
				ba, bia, coeff, off, cls := analyzeFormula(f)
				r.ByteAddress = ba
				r.BitAddress = bia
				r.Coefficient = coeff
				r.Offset = off
				r.FormulaClass = cls
				r.Unit = get("unit")
				r.MinValue = get("min_value")
				r.MaxValue = get("max_value")
			} else {
				r.RecordType = "dtc_table"
				r.IsDTC = true
				r.DTCCode = get("dtc_code")
				r.DTCDescription = get("dtc_description")
				r.RequestCommand = get("request_command")
				r.Formula = get("formula")
			}

			records = append(records, r)
		}
	}
	return records
}

// دانلود فایل و نسخه‌های قدیمی
func (c *Crawler) downloadFile(fl FileLink) FileResult {
	fr := FileResult{
		Kind:      "wiki_file",
		PageURL:   fl.PageURL,
		Name:      fl.Name,
		Ext:       fl.Ext,
		FetchedAt: nowISO(),
	}

	// ابتدا صفحه پرونده را می‌خوانیم تا لینک مستقیم فایل را پیدا کنیم
	data, _, err := c.fetchURL(fl.PageURL)
	if err != nil {
		fr.Current.Error = err.Error()
		return fr
	}

	doc, err := goquery.NewDocumentFromReader(bytes.NewReader(data))
	if err != nil {
		fr.Current.Error = err.Error()
		return fr
	}

	directURL := ""
	doc.Find("div.fullMedia a, div.mw-filepage-resolutioninfo a").Each(func(i int, s *goquery.Selection) {
		if href, ok := s.Attr("href"); ok && directURL == "" {
			directURL = c.resolveURL(mustURL(fl.PageURL), href)
		}
	})
	doc.Find("a.internal").Each(func(i int, s *goquery.Selection) {
		if href, ok := s.Attr("href"); ok && (strings.HasSuffix(href, ".json") || strings.HasSuffix(href, ".pdf")) {
			if directURL == "" {
				directURL = c.resolveURL(mustURL(fl.PageURL), href)
			}
		}
	})

	if directURL == "" {
		fr.Current.Error = "direct link not found"
		return fr
	}

	// دانلود فایل اصلی
	fileData, contentType, err := c.fetchBytes(directURL)
	if err != nil {
		fr.Current.Error = err.Error()
		return fr
	}

	sha := sha256.Sum256(fileData)
	shaHex := hex.EncodeToString(sha[:])

	outDir := filepath.Join(c.cfg.OutDir, "files", "json")
	if fl.Ext == "pdf" {
		outDir = filepath.Join(c.cfg.OutDir, "files", "pdf")
	} else if fl.Ext != "json" {
		outDir = filepath.Join(c.cfg.OutDir, "files", "other")
	}
	os.MkdirAll(outDir, 0755)

	localPath := filepath.Join(outDir, fl.Name)
	os.WriteFile(localPath, fileData, 0644)

	fr.Current = FileVersion{
		URL:          directURL,
		Timestamp:    "current",
		LocalPath:    localPath,
		SHA256:       shaHex,
		Size:         int64(len(fileData)),
		ContentType:  contentType,
		LastModified: nowISO(),
	}

	// اعتبارسنجی JSON
	if fl.Ext == "json" {
		valid := json.Valid(fileData)
		fr.Current.ValidJSON = &valid
	}

	// تاریخچه نسخه‌ها
	if c.cfg.History && fl.Ext == "json" {
		fr.History = c.fetchFileHistory(fl.PageURL, fl.Name)
	}

	return fr
}

func mustURL(s string) *url.URL {
	u, _ := url.Parse(s)
	return u
}

func (c *Crawler) fetchFileHistory(pageURL string, fileName string) []FileVersion {
	var history []FileVersion
	// صفحه تاریخچه پرونده
	histURL := pageURL + "?action=history"
	data, _, err := c.fetchURL(histURL)
	if err != nil {
		return history
	}

	doc, err := goquery.NewDocumentFromReader(bytes.NewReader(data))
	if err != nil {
		return history
	}

	// پیدا کردن لینک‌های نسخه قدیمی
	doc.Find("a").Each(func(i int, s *goquery.Selection) {
		href, _ := s.Attr("href")
		if strings.Contains(href, "oldid=") || strings.Contains(href, "timestamp=") {
			resolved := c.resolveURL(mustURL(pageURL), href)
			if resolved != "" {
				// سعی در دانلود نسخه قدیمی
				oldData, _, err := c.fetchBytes(resolved)
				if err == nil && len(oldData) > 0 {
					sha := sha256.Sum256(oldData)
					ts := time.Now().UTC().Format("20060102150405")
					histDir := filepath.Join(c.cfg.OutDir, "files", "history")
					os.MkdirAll(histDir, 0755)
					histName := fmt.Sprintf("%s__%s.%s", strings.TrimSuffix(fileName, filepath.Ext(fileName)), ts, filepath.Ext(fileName))
					lp := filepath.Join(histDir, histName)
					os.WriteFile(lp, oldData, 0644)
					history = append(history, FileVersion{
						URL:       resolved,
						Timestamp: ts,
						DateText:  clean(s.Text()),
						LocalPath: lp,
						SHA256:    hex.EncodeToString(sha[:]),
						Size:      int64(len(oldData)),
					})
				}
			}
		}
	})

	return history
}

// مقایسه سایت با GitHub
func (c *Crawler) compareWithGitHub(fileResults map[string]FileResult) {
	if !c.cfg.GitHub {
		return
	}

	for name, fr := range fileResults {
		if fr.Ext != "json" {
			continue
		}
		ghURL := c.cfg.GitHubRaw + name
		<-c.ghLim.C
		ghData, _, err := c.fetchBytes(ghURL)
		check := GitHubCheck{
			Name:      name,
			URL:       ghURL,
			SiteSHA:   fr.Current.SHA256,
			LocalPath: fr.Current.LocalPath,
			CheckedAt: nowISO(),
		}
		if err != nil {
			check.Status = "error"
			check.Error = err.Error()
		} else {
			ghSha := sha256.Sum256(ghData)
			check.GitHubSHA = hex.EncodeToString(ghSha[:])
			if check.SiteSHA == check.GitHubSHA {
				check.Status = "identical"
			} else {
				check.Status = "differs"
				// ذخیره نسخه گیت‌هاب برای مقایسه
				ghDir := filepath.Join(c.cfg.OutDir, "files", "github_json")
				os.MkdirAll(ghDir, 0755)
				os.WriteFile(filepath.Join(ghDir, name), ghData, 0644)
			}
		}
		c.ghChecks = append(c.ghChecks, check)
	}
}

// مقایسه جدول صفحه با فایل JSON
func (c *Crawler) compareSiteTableVsJSON(pageRecords []ECURecord, fileRecords map[string][]ECURecord) {
	// گروه‌بندی رکوردهای فایل JSON بر اساس صفحه ECU
	fileByPage := map[string][]ECURecord{}
	for _, recs := range fileRecords {
		for _, r := range recs {
			if r.EcuPageURL != "" {
				fileByPage[r.EcuPageURL] = append(fileByPage[r.EcuPageURL], r)
			}
		}
	}

	// برای هر رکورد جدول، بررسی وجود در JSON
	for _, pr := range pageRecords {
		if pr.SourceKind != "site_page_table" {
			continue
		}
		found := false
		for _, fr := range fileByPage[pr.EcuPageURL] {
			if normCmd(fr.RequestCommand) == normCmd(pr.RequestCommand) &&
				norm(fr.ParameterName) == norm(pr.ParameterName) {
				found = true
				// مقایسه فرمول
				tableFormula := identityStrip(pr.Formula)
				jsonFormula := identityStrip(fr.Formula)
				status := "formula_differs"
				if tableFormula == jsonFormula {
					status = "formula_identical"
				} else if tableFormula != "" && jsonFormula != "" {
					// بررسی هم‌ارزی ریاضی ساده
					status = "formula_equivalent"
				}
				c.siteVsJSON = append(c.siteVsJSON, map[string]interface{}{
					"ecu_page":      pr.EcuPageURL,
					"ecu_title":     pr.PageTitle,
					"file":          fr.FileName,
					"parameter":     pr.ParameterName,
					"command":       pr.RequestCommand,
					"table_formula": pr.Formula,
					"json_formula":  fr.Formula,
					"table_min":     pr.MinValue,
					"json_min":      fr.MinValue,
					"table_max":     pr.MaxValue,
					"json_max":      fr.MaxValue,
					"table_unit":    pr.Unit,
					"json_unit":     fr.Unit,
					"status":        status,
					"vehicle":       pr.VehicleModel,
				})
				break
			}
		}
		if !found {
			c.siteVsJSON = append(c.siteVsJSON, map[string]interface{}{
				"ecu_page":      pr.EcuPageURL,
				"ecu_title":     pr.PageTitle,
				"file":          "",
				"parameter":     pr.ParameterName,
				"command":       pr.RequestCommand,
				"table_formula": pr.Formula,
				"table_min":     pr.MinValue,
				"table_max":     pr.MaxValue,
				"table_unit":    pr.Unit,
				"status":        "not_in_json",
				"vehicle":       pr.VehicleModel,
			})
		}
	}
}

// worker
func (c *Crawler) worker() {
	for task := range c.queue {
		func() {
			defer c.pending.Done()

			if task.Kind == "page" {
				atomic.AddInt64(&c.pages, 1)
				data, _, err := c.fetchURL(task.URL)
				if err != nil {
					c.mu.Lock()
					c.errors = append(c.errors, ErrorEntry{
						URL:   task.URL,
						Stage: "fetch_page",
						Error: err.Error(),
						At:    nowISO(),
					})
					c.mu.Unlock()
					return
				}

				pr := c.parsePage(task.URL, data)

				// استخراج رکوردها از جداول
				tableRecs := c.extractTableRecords(pr)
				pr.RecordCount = len(tableRecs)

				c.mu.Lock()
				c.pageResults[task.URL] = pr
				c.records = append(c.records, tableRecs...)
				c.mu.Unlock()

				// صف‌بندی لینک‌های داخلی
				for _, l := range pr.Links {
					c.enqueue("page", l)
				}
				// صف‌بندی فایل‌ها
				for _, fl := range pr.FileLinks {
					c.enqueue("file", fl.PageURL)
					// ذخیره اطلاعات فایل لینک برای پردازش بعدی
					c.mu.Lock()
					// نگهداری موقت FileLink
					c.mu.Unlock()
				}

				// ذخیره در JSONL برای resume
				c.mu.Lock()
				if c.pagesF != nil {
					b, _ := json.Marshal(pr)
					c.pagesF.Write(append(b, '\n'))
				}
				c.mu.Unlock()

			} else if task.Kind == "file" {
				fl := FileLink{PageURL: task.URL, Name: path.Base(task.URL)}
				ext := strings.ToLower(filepath.Ext(fl.Name))
				fl.Ext = strings.TrimPrefix(ext, ".")

				fr := c.downloadFile(fl)

				c.mu.Lock()
				c.fileResults[fl.Name] = fr
				if c.filesF != nil {
					b, _ := json.Marshal(fr)
					c.filesF.Write(append(b, '\n'))
				}
				c.mu.Unlock()

				// اگر JSON بود، رکوردها را استخراج کن
				if fl.Ext == "json" && fr.Current.Error == "" {
					data, _ := os.ReadFile(fr.Current.LocalPath)
					// پیدا کردن صفحه ECU مرتبط
					ecuPage := ""
					c.mu.Lock()
					for _, pr := range c.pageResults {
						for _, fl2 := range pr.FileLinks {
							if fl2.Name == fl.Name {
								ecuPage = pr.URL
								break
							}
						}
						if ecuPage != "" {
							break
						}
					}
					c.mu.Unlock()

					recs := c.parseSicarJSON(data, fl.Name, fr.Current.SHA256, fr.Current.URL, ecuPage)
					if len(recs) > 0 {
						c.mu.Lock()
						c.records = append(c.records, recs...)
						if c.recordsF != nil {
							for _, r := range recs {
								b, _ := json.Marshal(r)
								c.recordsF.Write(append(b, '\n'))
							}
						}
						c.mu.Unlock()
					}
				}
			}
		}()
	}
}

func (c *Crawler) Run() {
	os.MkdirAll(c.cfg.OutDir, 0755)
	os.MkdirAll(filepath.Join(c.cfg.OutDir, "files", "json"), 0755)
	os.MkdirAll(filepath.Join(c.cfg.OutDir, "files", "pdf"), 0755)
	os.MkdirAll(filepath.Join(c.cfg.OutDir, "files", "history"), 0755)
	os.MkdirAll(filepath.Join(c.cfg.OutDir, "state"), 0755)

	// فایل‌های state برای resume
	stateDir := filepath.Join(c.cfg.OutDir, "state")
	if c.cfg.Fresh {
		os.RemoveAll(stateDir)
		os.MkdirAll(stateDir, 0755)
	}

	c.pagesF, _ = os.OpenFile(filepath.Join(stateDir, "pages.jsonl"), os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
	c.filesF, _ = os.OpenFile(filepath.Join(stateDir, "files.jsonl"), os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
	c.recordsF, _ = os.OpenFile(filepath.Join(stateDir, "records.jsonl"), os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
	c.errorsF, _ = os.OpenFile(filepath.Join(stateDir, "errors.jsonl"), os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
	defer c.pagesF.Close()
	defer c.filesF.Close()
	defer c.recordsF.Close()
	defer c.errorsF.Close()

	// بارگذاری state قبلی
	c.loadState(stateDir)

	fmt.Printf("[*] SICAR Crawler v%s starting...\n", Version)
	fmt.Printf("[*] Base URL: %s\n", c.cfg.BaseURL)
	fmt.Printf("[*] Output: %s\n", c.cfg.OutDir)
	fmt.Printf("[*] Workers: %d, Rate: %v\n", c.cfg.Workers, c.cfg.Rate)

	c.loadRobots()
	fmt.Printf("[*] robots.txt: %s\n", c.robotsStatus)

	// شروع workerها
	for i := 0; i < c.cfg.Workers; i++ {
		go c.worker()
	}

	// صف‌بندی صفحه شروع
	c.enqueue("page", c.cfg.StartURL)

	// نمایش پیشرفت
	ticker := time.NewTicker(5 * time.Second)
	quitTicker := make(chan struct{})
	go func() {
		for {
			select {
			case <-ticker.C:
				c.mu.Lock()
				np := len(c.pageResults)
				nf := len(c.fileResults)
				nr := len(c.records)
				ne := len(c.errors)
				c.mu.Unlock()
				fmt.Printf("[.] pages=%d files=%d records=%d errors=%d\n", np, nf, nr, ne)
			case <-quitTicker:
				ticker.Stop()
				return
			}
		}
	}()

	// منتظر اتمام تمام کارها
	c.pending.Wait()
	close(c.queue)   // توقف Workers
	close(quitTicker) // توقف گوروتین پیشرفت

	fmt.Printf("[*] Crawling complete. Pages: %d, Files: %d, Records: %d, Errors: %d\n",
		len(c.pageResults), len(c.fileResults), len(c.records), len(c.errors))

	// ساخت درخت خودروها از بخش‌های صفحه اصلی
	c.buildVehiclesTree()

	// مقایسه جدول صفحه با فایل JSON
	fileRecs := map[string][]ECURecord{}
	for _, r := range c.records {
		if r.SourceKind == "site_json_file" {
			fileRecs[r.FileName] = append(fileRecs[r.FileName], r)
		}
	}
	c.compareSiteTableVsJSON(c.records, fileRecs)

	// مقایسه با GitHub
	c.compareWithGitHub(c.fileResults)

	// ساخت تاریخچه نسخه
	c.buildVersionHistory()

	// ذخیره خروجی نهایی
	c.saveOutputs()

	fmt.Println("[✓] All outputs saved.")
}

func (c *Crawler) loadState(stateDir string) {
	// بارگذاری صفحات قبلی
	if f, err := os.Open(filepath.Join(stateDir, "pages.jsonl")); err == nil {
		sc := bufio.NewScanner(f)
		sc.Buffer(make([]byte, 1024*1024), 10*1024*1024)
		for sc.Scan() {
			var pr PageResult
			if json.Unmarshal(sc.Bytes(), &pr) == nil {
				c.pageResults[pr.URL] = pr
				c.seen.Store("page|"+pr.URL, true)
			}
		}
		f.Close()
	}
	// بارگذاری فایل‌های قبلی
	if f, err := os.Open(filepath.Join(stateDir, "files.jsonl")); err == nil {
		sc := bufio.NewScanner(f)
		sc.Buffer(make([]byte, 1024*1024), 10*1024*1024)
		for sc.Scan() {
			var fr FileResult
			if json.Unmarshal(sc.Bytes(), &fr) == nil {
				c.fileResults[fr.Name] = fr
				c.seen.Store("file|"+fr.PageURL, true)
			}
		}
		f.Close()
	}
	fmt.Printf("[*] Resumed: %d pages, %d files from previous run\n", len(c.pageResults), len(c.fileResults))
}

func (c *Crawler) buildVehiclesTree() {
	// از بخش‌های صفحه شروع
	for _, pr := range c.pageResults {
		for _, sec := range pr.Sections {
			c.vehiclesTree = append(c.vehiclesTree, sec)
			for _, vp := range sec.VehiclePages {
				c.makerByVehPage[vp] = sec.Manufacturer
			}
		}
	}
}

func (c *Crawler) buildVersionHistory() {
	for name, fr := range c.fileResults {
		if len(fr.History) == 0 {
			continue
		}
		entry := map[string]interface{}{
			"file":     name,
			"current":  fr.Current,
			"versions": append([]FileVersion{fr.Current}, fr.History...),
			"changes":  []map[string]interface{}{},
		}
		// مقایسه نسخه‌ها
		allVers := append([]FileVersion{fr.Current}, fr.History...)
		for i := 0; i < len(allVers)-1; i++ {
			if allVers[i].SHA256 != allVers[i+1].SHA256 {
				entry["changes"] = append(entry["changes"].([]map[string]interface{}), map[string]interface{}{
					"from": allVers[i+1].Timestamp,
					"to":   allVers[i].Timestamp,
					"diff": []string{"sha256 changed"},
				})
			}
		}
		c.versionHist = append(c.versionHist, entry)
	}
}

func (c *Crawler) saveOutputs() {
	out := c.cfg.OutDir

	// raw_records.json - تمام رکوردها
	allRecsJSON, _ := json.MarshalIndent(c.records, "", "  ")
	os.WriteFile(filepath.Join(out, "raw_records.json"), allRecsJSON, 0644)

	// vehicles_tree.json
	treeJSON, _ := json.MarshalIndent(c.vehiclesTree, "", "  ")
	os.WriteFile(filepath.Join(out, "vehicles_tree.json"), treeJSON, 0644)

	// site_vs_json.json
	svjJSON, _ := json.MarshalIndent(c.siteVsJSON, "", "  ")
	os.WriteFile(filepath.Join(out, "site_vs_json.json"), svjJSON, 0644)

	// github_vs_site.json
	ghsJSON, _ := json.MarshalIndent(c.ghChecks, "", "  ")
	os.WriteFile(filepath.Join(out, "github_vs_site.json"), ghsJSON, 0644)

	// version_history.json
	vhJSON, _ := json.MarshalIndent(c.versionHist, "", "  ")
	os.WriteFile(filepath.Join(out, "version_history.json"), vhJSON, 0644)

	// crawl_report.json
	report := map[string]interface{}{
		"version":          Version,
		"base_url":         c.cfg.BaseURL,
		"start_url":        c.cfg.StartURL,
		"finished_at":      nowISO(),
		"pages_total":      len(c.pageResults),
		"files_total":      len(c.fileResults),
		"records_total":    len(c.records),
		"errors_total":     len(c.errors),
		"workers":          c.cfg.Workers,
		"rate_limit":       c.cfg.Rate.String(),
		"robots_status":    c.robotsStatus,
		"vehicles_tree":    len(c.vehiclesTree),
		"site_vs_json":     len(c.siteVsJSON),
		"github_checks":    len(c.ghChecks),
		"version_history":  len(c.versionHist),
	}
	repJSON, _ := json.MarshalIndent(report, "", "  ")
	os.WriteFile(filepath.Join(out, "crawl_report.json"), repJSON, 0644)

	// errors.json
	if len(c.errors) > 0 {
		errJSON, _ := json.MarshalIndent(c.errors, "", "  ")
		os.WriteFile(filepath.Join(out, "errors.json"), errJSON, 0644)
	}

	// CSV خلاصه
	f, _ := os.Create(filepath.Join(out, "summary.csv"))
	w := csv.NewWriter(f)
	w.Write([]string{"Vehicle", "ECU", "Parameter", "Command", "Formula", "Unit", "Source", "File"})
	for _, r := range c.records {
		w.Write([]string{r.VehicleModel, r.ECUModel, r.ParameterName, r.RequestCommand,
			r.Formula, r.Unit, r.SourceURL, r.FileName})
	}
	w.Flush()
	f.Close()

	fmt.Printf("[✓] Outputs saved to %s\n", out)
	fmt.Printf("    - raw_records.json (%d records)\n", len(c.records))
	fmt.Printf("    - vehicles_tree.json (%d sections)\n", len(c.vehiclesTree))
	fmt.Printf("    - site_vs_json.json (%d comparisons)\n", len(c.siteVsJSON))
	fmt.Printf("    - github_vs_site.json (%d checks)\n", len(c.ghChecks))
	fmt.Printf("    - version_history.json (%d files with history)\n", len(c.versionHist))
	fmt.Printf("    - crawl_report.json\n")
	fmt.Printf("    - summary.csv\n")
}

func main() {
	cfg := parseFlags()
	crawler := NewCrawler(cfg)
	crawler.Run()
}
