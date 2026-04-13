"""
Advanced Auto-Rename Engine - Deep file analysis via regex + ffprobe.
Detects: episode, season, quality, vcodec, acodec, source, HDR, release group,
per-track audio codec+bitrate+channels, subtitle info, etc.

Template vars: {episode} {season} {quality} {vcodec} {acodec} {source} {hdr}
{audio} {audio_detail} {subs} {extension} {title} {group} {languages}
{channels} {abitrate} {resolution} {vbitrate}
"""
import os, re, json, logging, subprocess
from lang_map import get_language_label
logger = logging.getLogger(__name__)

# === REGEX PATTERNS ===
EP_PATS = [
    re.compile(r'[Ss](\d{1,3})\s*[Ee](\d{1,4})(?:v\d)?'),
    re.compile(r'[Ss](\d{1,3})\s*EP(\d{1,4})', re.I),
    re.compile(r'(?:Season|Series)\s*(\d{1,3})\s*(?:Episode|Ep\.?)\s*(\d{1,4})', re.I),
    re.compile(r'[\[\(]\s*[Ss](\d{1,3})[Ee](\d{1,4})\s*[\]\)]'),
    re.compile(r'[Ss](\d{1,3})\s*[-]\s*[Ee](\d{1,4})'),
    re.compile(r'(\d{1,3})[Xx](\d{1,4})'),
    re.compile(r'(?:Episode|Ep\.?)\s*[-]?\s*(\d{1,4})', re.I),
    re.compile(r'(?<=[\s._\[\(-])[Ee](\d{2,4})(?=[\s._\]\)\-]|$)'),
    re.compile(r'#(\d{1,4})'),
    re.compile(r'(?:Chapter|Ch\.?)\s*(\d{1,4})', re.I),
    re.compile(r'(?:Part|Pt\.?|Vol\.?|Volume)\s*(\d{1,4})', re.I),
    re.compile(r'(?:OVA|OAD|Special|SP|ONA)\s*(\d{1,3})', re.I),
    re.compile(r'(?<=[\s\]])[\-\u2013\u2014]\s*(\d{2,4})(?:v\d)?(?=[\s._\[\(]|$)'),
    re.compile(r'\[(\d{2,4})\]'),
    re.compile(r'[\s._\-]+(\d{2,4})[\s._\-]+(?:v\d)?'),
    re.compile(r'#(\d{1,4})'),
    re.compile(r'[\s._\-](\d{2,4})(?:v\d)?(?:\.\w{2,4})?$'),
]
SEASON_PATS = [re.compile(r'[Ss](\d{1,2})(?:[Ee\s]|$)'), re.compile(r'Season\s*(\d{1,2})',re.I)]
QUALITY_PAT = re.compile(r'(?:^|[\s._\-\[(])(2160[pP]|4[Kk]|1080[pP]|720[pP]|480[pP]|360[pP]|540[pP])(?:[\s._\-\])]|$)')
VCODEC_PATS = [(re.compile(r'(?:x\.?265|[Hh]\.?265|HEVC)',re.I),"HEVC"),(re.compile(r'(?:x\.?264|[Hh]\.?264|AVC)',re.I),"x264"),(re.compile(r'AV1',re.I),"AV1"),(re.compile(r'VP9',re.I),"VP9")]
ACODEC_PATS = [(re.compile(r'\bDDP\b|\bDD[+]\b|\bE-?AC-?3\b',re.I),"DD+"),(re.compile(r'\bDD\s*\d|\bAC-?3\b',re.I),"DD"),(re.compile(r'\bAAC\b',re.I),"AAC"),(re.compile(r'\bFLAC\b',re.I),"FLAC"),(re.compile(r'\bOpus\b',re.I),"Opus"),(re.compile(r'\bMP3\b',re.I),"MP3"),(re.compile(r'\bDTS-?HD',re.I),"DTS-HD MA"),(re.compile(r'\bDTS\b',re.I),"DTS"),(re.compile(r'\bTrueHD\b|\bAtmos\b',re.I),"TrueHD")]
SOURCE_PATS = [(re.compile(r'\bAMZN\b',re.I),"AMZN"),(re.compile(r'\bNF\b|Netflix',re.I),"NF"),(re.compile(r'\bDSNP\b',re.I),"DSNP"),(re.compile(r'\bHMAX\b',re.I),"HMAX"),(re.compile(r'\bATVP\b',re.I),"ATVP"),(re.compile(r'\bPCOK\b',re.I),"PCOK"),(re.compile(r'\bTPLAY\b',re.I),"TPLAY"),(re.compile(r'\bJIOTV\b',re.I),"JIOTV"),(re.compile(r'\bSHEMAROO\b',re.I),"SHEMAROO"),(re.compile(r'\bXSTREAM\b',re.I),"XSTREAM"),(re.compile(r'\bMANGOMAN\b',re.I),"MANGOMAN"),(re.compile(r'\bHULU\b',re.I),"HULU"),(re.compile(r'\bWEB-?DL\b',re.I),"WEB-DL"),(re.compile(r'\bWEB-?Rip\b',re.I),"WEBRip"),(re.compile(r'\bBlu-?Ray\b|\bBDRip\b',re.I),"BluRay"),(re.compile(r'\bHDRip\b',re.I),"HDRip"),(re.compile(r'\bHDTV\b',re.I),"HDTV"),(re.compile(r'\bCR\b|Crunchyroll',re.I),"CR"),(re.compile(r'\bFUNI\b',re.I),"FUNI"),(re.compile(r'\bTUBI\b',re.I),"TUBI"),(re.compile(r'\bDVDRip\b',re.I),"DVDRip"),(re.compile(r'\bCAM\b',re.I),"CAM")]
HDR_PATS = [(re.compile(r'\bHDR10\+\b',re.I),"HDR10+"),(re.compile(r'\bHDR10\b',re.I),"HDR10"),(re.compile(r'\bDolby\s*Vision\b|\bDV\b|\bDoVi\b',re.I),"DV"),(re.compile(r'\bHDR\b',re.I),"HDR"),(re.compile(r'\bHLG\b',re.I),"HLG"),(re.compile(r'\bSDR\b',re.I),"SDR")]
GROUP_PAT = re.compile(r'\[([A-Za-z][A-Za-z0-9\-_.]{1,30})\]')
CHANNELS_PAT = re.compile(r'(\d\.\d)(?:\s*ch)?',re.I)
BITRATE_PAT = re.compile(r'(\d{2,4})\s*[Kk][Bb](?:ps)?',re.I)

LANG_SHORT = {"hin":"Hin","hindi":"Hin","eng":"Eng","english":"Eng","tam":"Tam","tamil":"Tam","tel":"Tel","telugu":"Tel","mal":"Mal","malayalam":"Mal","kan":"Kan","ben":"Ben","mar":"Mar","jpn":"Jap","japanese":"Jap","kor":"Kor","korean":"Kor","spa":"Spa","fre":"Fre","ger":"Ger","por":"Por","rus":"Rus","ita":"Ita","ara":"Ara","urd":"Urd","pan":"Pun","und":"Und","chi":"Chi","zh":"Chi","hi":"Hin","en":"Eng","ta":"Tam","te":"Tel","ml":"Mal","kn":"Kan","bn":"Ben","mr":"Mar","ja":"Jap","ko":"Kor","gu":"Guj","pa":"Pun","th":"Tha","vi":"Vie","ne":"Nep","as":"Asm","or":"Odi"}

def analyze_filename(filename):
    name, ext = filename, ""
    if "." in name:
        p = name.rsplit(".",1)
        if len(p[1])<=5: name,ext = p[0],p[1].lower()
    r = {"original_name":filename,"extension":ext,"episode":"","season":"","quality":"","vcodec":"","acodec":"","source":"","hdr":"","group":"","channels":"","abitrate":"","languages_from_name":[],"title":"","bitdepth":"","year":"","filename":filename,"filename_no_ext":name}
    for i,pat in enumerate(EP_PATS):
        m = pat.search(name)
        if m:
            g = m.groups()
            if len(g)==2: r["season"]=str(int(g[0])).zfill(2);r["episode"]=str(int(g[1])).zfill(2)
            elif len(g)==1 and int(g[0])<1900: r["episode"]=str(int(g[0])).zfill(2)
            break
    if not r["season"]:
        for pat in SEASON_PATS:
            m=pat.search(name)
            if m: r["season"]=str(int(m.group(1))).zfill(2);break
    m=QUALITY_PAT.search(name)
    if m: r["quality"]="2160p" if m.group(1).lower()=="4k" else m.group(1).lower()
    for pat,lab in VCODEC_PATS:
        if pat.search(name): r["vcodec"]=lab;break
    for pat,lab in ACODEC_PATS:
        if pat.search(name): r["acodec"]=lab;break
    for pat,lab in SOURCE_PATS:
        if pat.search(name): r["source"]=lab;break
    for pat,lab in HDR_PATS:
        if pat.search(name): r["hdr"]=lab;break
    gs=GROUP_PAT.findall(name)
    skip={"1080p","720p","480p","360p","540p","2160p","4k","hevc","x265","x264","avc","av1","vp9","aac","flac","opus","mp3","dd","ddp","ac3","eac3","dts","truehd","atmos","dual audio","multi","10bit","8bit","hdr","hdr10","sdr","web-dl","webrip","bluray","amzn","nf","dsnp","msubs","esubs","subs"}
    for g in gs:
        if g.lower() not in skip: r["group"]=g;break
    m=CHANNELS_PAT.search(name)
    if m: r["channels"]=m.group(1)
    m=BITRATE_PAT.search(name)
    if m: r["abitrate"]=f"{m.group(1)}Kbps"
    import re as _r
    _bd=_r.search(r'\b(10)[- ]?bit\b|\b(8)[- ]?bit\b',name,_r.I)
    if _bd: r["bitdepth"]="10bit" if _bd.group(1) else "8bit"
    _yr=_r.search(r'(?:^|[\s\(\[\-])(\d{4})(?:[\s\)\]\-]|$)',name)
    if _yr:
        _y=int(_yr.group(1))
        if 1950<=_y<=2099: r["year"]=str(_y)
    return r

def _ffprobe_json(fp):
    try:
        res=subprocess.run(["ffprobe","-v","error","-print_format","json","-show_format","-show_streams",fp],capture_output=True,text=True,timeout=60)
        return json.loads(res.stdout)
    except: return {}

def analyze_file(file_path, filename=""):
    if not filename: filename=os.path.basename(file_path)
    info=analyze_filename(filename)
    probe=_ffprobe_json(file_path)
    if not probe: return info
    streams=probe.get("streams",[])
    vs=[s for s in streams if s.get("codec_type")=="video"]
    aus=[s for s in streams if s.get("codec_type")=="audio"]
    ss=[s for s in streams if s.get("codec_type")=="subtitle"]
    if vs:
        v=vs[0];w=v.get("width",0);h=v.get("height",0)
        info["resolution"]=f"{w}x{h}" if w and h else ""
        if not info["quality"] and h:
            info["quality"]="2160p" if h>=2000 else "1080p" if h>=1000 else "720p" if h>=700 else "540p" if h>=500 else "480p" if h>=400 else "360p"
        vc=v.get("codec_name","").lower()
        cm={"hevc":"HEVC","h265":"HEVC","h264":"x264","avc":"x264","av1":"AV1","vp9":"VP9"}
        if not info["vcodec"] and vc: info["vcodec"]=cm.get(vc,vc.upper())
        vbr=v.get("bit_rate")
        if vbr: info["vbitrate"]=f"{int(vbr)//1000}Kbps"
        if not info["bitdepth"]:
            pix=v.get("pix_fmt","")
            if "10" in pix or "p010" in pix: info["bitdepth"]="10bit"
            elif pix: info["bitdepth"]="8bit"
        if not info["hdr"]:
            ct=v.get("color_transfer","");cp=v.get("color_primaries","");sd=v.get("side_data_list",[])
            if any("dovi" in str(x).lower() for x in sd): info["hdr"]="DV"
            elif "smpte2084" in ct: info["hdr"]="HDR10" if "bt2020" in cp else "HDR"
            elif "arib-std-b67" in ct: info["hdr"]="HLG"
            else: info["hdr"]="SDR"
    tracks=[]
    for a in aus:
        tags=a.get("tags",{});lc=tags.get("language","und").lower()
        ls=LANG_SHORT.get(lc,get_language_label(lc)[:3])
        cn=a.get("codec_name","").lower()
        acm={"aac":"AAC","ac3":"DD","eac3":"DD+","flac":"FLAC","opus":"Opus","mp3":"MP3","dts":"DTS","truehd":"TrueHD"}
        cl=acm.get(cn,cn.upper())
        ch=a.get("channels",0)
        chl={1:"1.0",2:"2.0",6:"5.1",8:"7.1"}.get(ch,f"{ch}ch")
        br=a.get("bit_rate")
        brk=f"{int(br)//1000}Kbps" if br else ""
        tracks.append({"lang":ls,"codec":cl,"channels":chl,"bitrate":brk,"title":tags.get("title","")})
    info["audio_tracks"]=tracks
    info["audio"]=_smart_audio(tracks)
    info["audio_detail"]=" | ".join(f"{t['lang']} {t['codec']} {t['channels']} {t['bitrate']}".strip() for t in tracks)
    if tracks: info["languages"]=" ".join(t["lang"] for t in tracks)
    if tracks and not info["acodec"]: info["acodec"]=tracks[0]["codec"]
    if tracks and not info["channels"]: info["channels"]=tracks[0]["channels"]
    if tracks and not info["abitrate"]: info["abitrate"]=tracks[0].get("bitrate","")
    sl=[]
    for s in ss:
        lc=s.get("tags",{}).get("language","und").lower()
        ls=LANG_SHORT.get(lc,get_language_label(lc)[:3])
        if ls not in sl: sl.append(ls)
    info["subs"]=" ".join(sl)
    return info

def _smart_audio(tracks):
    """Group same codec+bitrate together: 'Hin Eng AAC 2.0 192Kbps Tam DD+ 5.1 384Kbps'"""
    if not tracks: return ""
    groups=[];cur=None
    for t in tracks:
        key=(t["codec"],t["channels"],t["bitrate"])
        if cur and cur["key"]==key: cur["langs"].append(t["lang"])
        else:
            if cur: groups.append(cur)
            cur={"key":key,"langs":[t["lang"]]}
    if cur: groups.append(cur)
    parts=[]
    for g in groups:
        c,ch,br=g["key"];ls=" ".join(g["langs"])
        d=f"{c}";
        if ch: d+=f" {ch}"
        if br: d+=f" {br}"
        parts.append(f"{ls} {d}")
    return " ".join(parts)

def apply_template(template, info):
    r=template
    for f in ["episode","season","quality","vcodec","acodec","source","hdr","group","channels","abitrate","extension","title","languages","audio","audio_detail","subs","resolution","vbitrate","bitdepth","year","filename","filename_no_ext"]:
        r=r.replace("{"+f+"}",str(info.get(f,"")))
    r=re.sub(r'\[\s*\]','',r);r=re.sub(r'\(\s*\)','',r)
    r=re.sub(r'\s*[\-|]+\s*$','',r);r=re.sub(r'\s{2,}',' ',r)
    return r.strip(" .-_")

def get_caption_from_original(file_path):
    return os.path.basename(file_path)
