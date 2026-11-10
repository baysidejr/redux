"""Original adapters for Batocera's public scraper service choices.

Application credentials are supplied by the user/deployment, never borrowed from
another frontend. JSON requests are bounded, cancellable and HTTPS-only. This
module returns choices; it does not change a game, download ROMs or upload media.
See docs/SCRAPER_PROVIDERS.md for API references and acceptance limits.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import base64
import json
import re
import threading
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

from . import __version__


MAX_JSON_BYTES = 12 * 1024 * 1024
MAX_CANDIDATES = 30
TIMEOUT = 12
USER_AGENT = f"ReduxMediaLibrary/{__version__} (+independent artwork client)"


class ScraperError(RuntimeError):
    code = "scraper"
    def __init__(self, message, *, provider="", status=None):
        super().__init__(message)
        self.provider, self.status = provider, status


class AuthError(ScraperError):
    code = "authentication"


class QuotaError(ScraperError):
    code = "quota"


class ProviderUnavailable(ScraperError):
    code = "unavailable"


class ScrapeCancelled(ScraperError):
    code = "cancelled"


@dataclass(frozen=True)
class ScrapeCandidate:
    provider: str
    id: str
    title: str
    system: str
    region: str = ""
    year: int | None = None
    publisher: str = ""
    developer: str = ""
    genre: str = ""
    description: str = ""
    # Authenticated media URLs can contain credentials; never expose in repr.
    media: dict[str, str] = field(default_factory=dict, repr=False)


_SS = {"nes":3,"snes":4,"n64":14,"genesis":1,"32x":19,"sega_cd":20,
       "saturn":22,"psx":57,"neo_geo_cd":70,"pce":31,"pce_cd":114,
       "supergrafx":105,"atari2600":26,"atari7800":41,"gb":9,"gbc":10,
       "gba":12,"sms":2,"game_gear":21,"arcade":75,"neo_geo":142}
_TG = {"pc":1,"nes":7,"snes":6,"n64":3,"genesis":18,"32x":33,"sega_cd":21,
       "saturn":17,"psx":10,"neo_geo_cd":4956,"pce":34,"pce_cd":4955,
       "supergrafx":34,"atari2600":22,"atari7800":27,"gb":4,"gbc":41,
       "gba":5,"sms":35,"game_gear":20,"arcade":23,"neo_geo":24}
_HF = {"nes":36012,"snes":82351,"n64":32765,"genesis":33149,"32x":33731,
       "sega_cd":83493,"saturn":82924,"psx":82937,"neo_geo_cd":237944,
       "pce":30273,"pce_cd":138489,"supergrafx":30443,"atari2600":84224,
       "atari7800":84225,"gb":36022,"gbc":89235,"gba":89234,"sms":82166,
       "game_gear":33807,"arcade":29794,"neo_geo":29794}
_IG = {"nes":18,"snes":19,"n64":4,"genesis":29,"32x":30,"sega_cd":78,
       "saturn":32,"psx":7,"neo_geo_cd":136,"pce":86,"pce_cd":150,
       "supergrafx":128,"atari2600":59,"atari7800":60,"gb":33,"gbc":22,
       "gba":24,"sms":64,"game_gear":35,"arcade":52,"neo_geo":79}
_LR = {"nes":"Nintendo_-_Nintendo_Entertainment_System", "snes":"Nintendo_-_Super_Nintendo_Entertainment_System",
       'jaguar':'Atari_-_Jaguar','jaguar_cd':'Atari_-_Jaguar_CD',
       "3do":"The_3DO_Company_-_3DO",
       "n64":"Nintendo_-_Nintendo_64", "genesis":"Sega_-_Mega_Drive_-_Genesis",
       "32x":"Sega_-_32X", "sega_cd":"Sega_-_Mega-CD_-_Sega_CD", "saturn":"Sega_-_Saturn",
       "psx":"Sony_-_PlayStation", "neo_geo_cd":"SNK_-_Neo_Geo_CD", "pce":"NEC_-_PC_Engine_-_TurboGrafx_16",
       "pce_cd":"NEC_-_PC_Engine_CD_-_TurboGrafx-CD", "supergrafx":"NEC_-_PC_Engine_SuperGrafx",
       "atari2600":"Atari_-_2600", "atari7800":"Atari_-_7800", "gb":"Nintendo_-_Game_Boy",
       "gbc":"Nintendo_-_Game_Boy_Color", "gba":"Nintendo_-_Game_Boy_Advance",
       "sms":"Sega_-_Master_System_-_Mark_III", "game_gear":"Sega_-_Game_Gear"}


def _credential(key, label, secret=False, required=True):
    return dict(key=key, label=label, secret=secret, required=required)


def provider_info():
    """Return fresh UI-safe service descriptions, with no account values."""
    return [
        dict(id="screenscraper",name="ScreenScraper",systems=tuple(_SS),
             description="Box covers, screenshots, logos and mixed tiles. Requires a ScreenScraper account and this application's developer API credentials.",
             credentials=[_credential("username","Username"),_credential("password","Password",True),
                          _credential("developer_id","Developer API ID"),_credential("developer_password","Developer API password",True)]),
        dict(id="thegamesdb",name="TheGamesDB",systems=tuple(_TG),
             description="Game information, box covers, screenshots and fan art. Requires your own TheGamesDB API key.",
             credentials=[_credential("api_key","API key",True)]),
        dict(id="hfsdb",name="HFSDB",systems=tuple(_HF),
             description="Game information, covers and media. Requires an HFS API-enabled login; ordinary website account access is not guaranteed.",
             credentials=[_credential("username","HFS API username"),_credential("password","HFS API password",True)]),
        dict(id="igdb",name="IGDB",systems=tuple(_IG),
             description="Covers, screenshots and artwork through the IGDB/Twitch API. Requires your own Twitch developer client ID and secret.",
             credentials=[_credential("client_id","Client ID"),_credential("client_secret","Client secret",True)]),
        dict(id="arcadedb",name="ArcadeDB",systems=("arcade","neo_geo"),
             description="Arcade Database by motoschifo. Arcade ROM-set names only; no account required.",credentials=[]),
        dict(id="libretro",name="Libretro thumbnails",systems=tuple(_LR),
             description="Community box covers and screenshots matched by system, title and region. No account required.",credentials=[]),
    ]


def sanitize_url(url):
    """A provenance label, not a reusable authenticated download URL.

    Remove all query values, credentials, fragments and control characters. This
    also protects tokens with provider-specific parameter names we do not know.
    """
    try:
        if any(ord(c)<32 for c in str(url)):return ""
        parsed=urlsplit(str(url))
        if parsed.scheme not in {"http","https"} or not parsed.hostname:return ""
        host=parsed.hostname
        if parsed.port:host += ":"+str(parsed.port)
        query=urlencode([(key,"[redacted]") for key,_ in parse_qsl(parsed.query,keep_blank_values=True)])
        return urlunsplit((parsed.scheme,host,parsed.path,query,""))
    except (TypeError,ValueError):return ""


def _cancel(cancelled):
    if cancelled is not None and (cancelled.is_set() if hasattr(cancelled,"is_set") else cancelled()):
        raise ScrapeCancelled("Artwork lookup cancelled.")


class _SameOriginRedirect(HTTPRedirectHandler):
    max_redirections=3
    max_repeats=2
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        old,new=urlsplit(req.full_url),urlsplit(urljoin(req.full_url,newurl))
        if new.scheme!="https" or (new.hostname,new.port)!=(old.hostname,old.port):
            raise ProviderUnavailable("The artwork service redirected outside its API host.")
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def _status_error(status,url):
    """Provider-specific API statuses, without displaying an untrusted body."""
    screen=urlsplit(url).hostname in {"api.screenscraper.fr","www.screenscraper.fr","screenscraper.fr"}
    if screen:
        if status==401:return ProviderUnavailable("ScreenScraper is busy or this account does not currently have API access. Check the account or try again later.",status=status)
        if status==423:return ProviderUnavailable("ScreenScraper's API is temporarily closed. Try again later.",status=status)
        if status==426:return ProviderUnavailable("ScreenScraper does not accept this client version or developer authorization. Check the API setup or update Redux.",status=status)
        if status==430:return QuotaError("ScreenScraper's daily request limit was reached. Try again tomorrow.",status=status)
        if status==431:return QuotaError("ScreenScraper's daily limit for unrecognized games was reached. Try again tomorrow.",status=status)
    if status in {401,403}:return AuthError("The artwork service rejected the configured API credentials.",status=status)
    if status in {429,430}:return QuotaError("The artwork service request limit was reached. Try again later.",status=status)
    if isinstance(status,int) and status>=400:return ProviderUnavailable("The artwork service request failed.",status=status)
    return None


def _transport(url, *, method="GET", headers=None, data=None, cancelled=None):
    """Injectable JSON transport; never include response bodies or URLs in errors."""
    _cancel(cancelled)
    parsed=urlsplit(url)
    if parsed.scheme!="https" or not parsed.hostname or parsed.username or parsed.password:
        raise ProviderUnavailable("Artwork API requests require HTTPS.")
    request=Request(url,data=data,method=method,headers={"User-Agent":USER_AGENT,"Accept":"application/json",**(headers or {})})
    try:
        with build_opener(_SameOriginRedirect()).open(request,timeout=TIMEOUT) as response:
            length=response.headers.get("Content-Length")
            if length and int(length)>MAX_JSON_BYTES:raise ProviderUnavailable("The artwork service response exceeded its size limit.")
            content=bytearray()
            while True:
                _cancel(cancelled)
                block=response.read(min(65536,MAX_JSON_BYTES+1-len(content)))
                if not block:break
                content.extend(block)
                if len(content)>MAX_JSON_BYTES:raise ProviderUnavailable("The artwork service response exceeded its size limit.")
    except HTTPError as error:
        status=error.code
        error.close()
        raise _status_error(status,url) or ProviderUnavailable("The artwork service request failed.",status=status) from None
    except (URLError,TimeoutError,OSError,ValueError):
        raise ProviderUnavailable("The artwork service could not be reached. Check the connection and try again.") from None
    _cancel(cancelled)
    try:return json.loads(content.decode("utf-8-sig"))
    except (ValueError,UnicodeError):
        text=bytes(content).decode("utf-8",errors="ignore").casefold()
        if any(word in text for word in ("quota","too many requests","maximum de requ")):
            raise QuotaError("The artwork service request limit was reached. Try again later.") from None
        if any(word in text for word in ("login","password","identifiant","credentials","developer")):
            raise AuthError("The artwork service rejected the configured API credentials.") from None
        raise ProviderUnavailable("The artwork service returned an unsupported response.") from None


def _request(transport,url,*,params=None,method="GET",headers=None,data=None,cancelled=None):
    _cancel(cancelled)
    if params:url += ("&" if "?" in url else "?")+urlencode(params)
    try:
        result=transport(url,method=method,headers=headers,data=data,cancelled=cancelled)
    except ScraperError:raise
    except Exception:raise ProviderUnavailable("The artwork service request did not complete.") from None
    _cancel(cancelled)
    if isinstance(result,dict):
        status=result.get("code",result.get("status"))
        error=_status_error(int(status),url) if str(status).isdigit() else None
        if error:raise error
        failure=result.get("error") or result.get("errors") or result.get("erreur")
        if failure:
            # Classify without ever showing the server body, which may echo a key.
            label=str(failure).casefold()
            if any(part in label for part in ("quota","too many","limit reached")):
                raise QuotaError("The artwork service request limit was reached. Try again later.")
            if any(part in label for part in ("credential","password","unauthorized","invalid api","invalid token","login")):
                raise AuthError("The artwork service rejected the configured API credentials.")
            raise ProviderUnavailable("The artwork service could not complete this lookup.")
    elif not isinstance(result,list):raise ProviderUnavailable("The artwork service returned an unsupported response.")
    return result


def _required(credentials,*names):
    result=[]
    for name in names:
        value=credentials.get(name,"")
        if not isinstance(value,str) or not value.strip():
            raise AuthError("Complete this provider's account and API settings before scraping.")
        if len(value)>4096 or any(c in value for c in "\r\n\x00"):
            raise AuthError("The configured account or API value is invalid.")
        result.append(value)
    return result


def _text(value):
    if isinstance(value,dict):return _text(value.get("text",value.get("name","")))
    return str(value or "")[:16000]


def _localized(items, preference, key="region"):
    if isinstance(items,str):return items
    if isinstance(items,dict):
        if "text" in items:return _text(items)
        items=[dict(text=value,**{key:(name.rsplit('_',1)[-1] if key=="langue" else name)}) for name,value in items.items()]
    if not isinstance(items,list):return ""
    for preferred in preference:
        for item in items:
            if isinstance(item,dict) and str(item.get(key,""))==preferred:return _text(item)
    return _text(items[0]) if items else ""


def _year(value):
    match=re.search(r"(?:19|20)\d{2}",str(value or ""))
    return int(match[0]) if match else None


def _url(value,base=""):
    if not isinstance(value,str):return ""
    value=value.replace("#screenscraperserveur#","https://www.screenscraper.fr/")
    if value.startswith("//"):value="https:"+value
    if base:value=urljoin(base,value)
    parsed=urlsplit(value)
    # These public services still return some legacy http image links.
    if parsed.scheme=="http" and (parsed.hostname in {"adb.arcadeitalia.net","db.hfsplay.fr","cdn.thegamesdb.net"} or parsed.hostname and (parsed.hostname=="screenscraper.fr" or parsed.hostname.endswith(".screenscraper.fr"))):
        value=urlunsplit(("https",parsed.netloc,parsed.path,parsed.query,""));parsed=urlsplit(value)
    if parsed.scheme!="https" or not parsed.hostname or parsed.username or parsed.password:return ""
    return value


def _media_add(media,role,url,base=""):
    value=_url(url,base)
    if value and role not in media:media[role]=value


def _title(game):
    value=str(game.get("title","") or "").strip()
    # Strip dump region/language tags, retaining arbitrary edition/disc names.
    known={"usa","us","europe","eu","japan","jp","world","en","fr","de","es","it","sv","nl","pt","ja","ko","zh"}
    while match:=re.search(r"\s*[\[(]([^()\[\]]+)[\])]\s*$",value):
        if not all(part.strip().casefold() in known for part in match[1].split(",")):break
        value=value[:match.start()].strip()
    if not value or len(value)>240 or any(ord(c)<32 for c in value):raise ScraperError("Choose a game with a valid title before scraping.")
    return value


def _system(game,mapping):
    system=str(game.get("system", ""))
    if system not in mapping:raise ScraperError("This artwork provider does not support the selected game system.")
    return system,mapping[system]


def _region(value):
    raw=str(value or "").strip().casefold()
    return {"usa":"us","u":"us","us":"us","north america":"us","europe":"eu","e":"eu","eu":"eu","japan":"jp","j":"jp","jp":"jp","world":"wor"}.get(raw,raw if raw in {"us","eu","jp","wor"} else "")


def _game_region(game):
    """An identified release region wins; the scraper setting is its fallback."""
    return _region(game.get("region")) or _region(game.get("scrape_region"))


def _languages(game):
    language=str(game.get("scrape_language") or "en").casefold()
    if language not in {"en","fr","de","es","it","pt","ja","jp"}:language="en"
    return tuple(dict.fromkeys((language,*(('jp',) if language=='ja' else ()),"en","fr")))


def _hfs_localized(item,prefix,languages):
    for language in languages:
        value=_text(item.get(prefix+'_'+language))
        if value:return value
    return _text(item.get(prefix))


def _ss_medias(raw):
    """Accept API2's media array and documented named JSON media groups."""
    if isinstance(raw,list):return [m for m in raw if isinstance(m,dict)]
    result=[]
    if not isinstance(raw,dict):return result
    for key,value in raw.items():
        if isinstance(value,(dict,list)):
            result.extend(_ss_medias(value));continue
        if not isinstance(value,str) or key.endswith(("_md5","_sha1","_crc")):continue
        key=key.removeprefix("media_")
        typ=next((kind for prefix,kind in (("box2d","box-2D"),("boxs2d","box-2D"),("screenshot","ss"),("fanart","fanart"),("wheel","wheel"),("marquee","marquee"),("mix","mix")) if key.startswith(prefix)),key)
        region=key.rsplit('_',1)[-1] if '_' in key else ''
        result.append(dict(type=typ,region=region,url=value))
    return result


def _screenscraper(game,credentials,transport,cancelled):
    system,platform=_system(game,_SS)
    user,password,developer,devpass=_required(credentials,"username","password","developer_id","developer_password")
    try:
        data=_request(transport,"https://api.screenscraper.fr/api2/jeuRecherche.php",params={"devid":developer,"devpassword":devpass,"softname":"ReduxMediaLibrary","output":"json","ssid":user,"sspassword":password,"systemeid":platform,"recherche":_title(game)},cancelled=cancelled)
    except ProviderUnavailable as error:
        # ScreenScraper documents HTTP 404 as a genuine game/ROM miss.
        if error.status==404:return []
        raise
    response=data.get("response",data)
    if not isinstance(response,dict):raise ProviderUnavailable("ScreenScraper returned an unsupported response.")
    entries=response.get("jeux",[])
    if isinstance(entries,dict):entries=list(entries.values())
    if not isinstance(entries,list):raise ProviderUnavailable("ScreenScraper returned an unsupported game list.")
    region=_game_region(game);preferences=tuple(dict.fromkeys((region,"wor","us","eu","jp","ss","")))
    languages=_languages(game)
    result=[]
    for item in entries[:MAX_CANDIDATES]:
        if not isinstance(item,dict):continue
        item_system=item.get("systeme",{})
        if isinstance(item_system,dict) and item_system.get("id") is not None and str(item_system['id'])!=str(platform):continue
        title=_localized(item.get("noms",[]),preferences)
        if not title or not item.get("id"):continue
        media={};raw=_ss_medias(item.get("medias",[]))
        if isinstance(raw,list):
            if region:
                # Do not claim a USA projection while silently using JP artwork.
                # World and explicitly regionless assets remain valid fallbacks.
                raw=[m for m in raw if str(m.get("region","")).strip().casefold() in {"","wor","world"} or _region(m.get("region"))==region]
            # Prioritize matching region without excluding regionless screenshots.
            raw=sorted((m for m in raw if isinstance(m,dict)),key=lambda m:preferences.index(str(m.get("region",""))) if str(m.get("region","")) in preferences else len(preferences))
            kinds={"cover":("box-2D","box-2D-front","box2d-front"),"tile":("mixrbv2","mixrbv1","mixrbv","mix"),"hero":("fanart",),"screenshot":("ss","screenshot","sstitle"),"logo":("wheel-hd","wheel","marquee")}
            for role,types in kinds.items():
                for kind in types:
                    for m in raw:
                        typ=str(m.get("type",""))
                        if typ==kind or typ.startswith(kind+"_"):_media_add(media,role,m.get("url",m.get("text","")))
        genres=item.get("genres",[])
        genre=", ".join(filter(None,(_localized(g.get("noms",[]),languages,"langue") for g in genres if isinstance(g,dict)))) if isinstance(genres,list) else ""
        result.append(ScrapeCandidate("screenscraper",str(item["id"]),title,system,region,
            _year(_localized(item.get("dates",[]),preferences)),_text(item.get("editeur")),_text(item.get("developpeur")),genre,
            _localized(item.get("synopsis",[]),languages,"langue"),media))
    return result


def _tg_media(data,game_id):
    media={}
    if not isinstance(data,dict):return media
    base=data.get("base_url",{})
    base=base.get("original",base.get("large",base.get("medium",""))) if isinstance(base,dict) else ""
    images=data.get("images",data.get("data",{}));images=images.get(str(game_id),[]) if isinstance(images,dict) else []
    for image in images if isinstance(images,list) else []:
        if not isinstance(image,dict):continue
        kind=image.get("type");side=image.get("side","")
        role="cover" if kind=="boxart" and side=="front" else {"screenshot":"screenshot","fanart":"hero","clearlogo":"logo"}.get(kind)
        if role:_media_add(media,role,urljoin(base,str(image.get("filename",""))))
    return media


def _thegamesdb(game,credentials,transport,cancelled):
    system,platform=_system(game,_TG);key,=_required(credentials,"api_key")
    data=_request(transport,"https://api.thegamesdb.net/v1/Games/ByGameName",params={"apikey":key,"name":_title(game),"filter[platform]":platform,"fields":"overview,publishers,genres","include":"boxart"},cancelled=cancelled)
    entries=data.get("data",{}).get("games",[])
    if isinstance(entries,dict):entries=list(entries.values())
    if not isinstance(entries,list):raise ProviderUnavailable("TheGamesDB returned an unsupported game list.")
    boxart=data.get("include",{}).get("boxart",{})
    result=[]
    for item in entries[:MAX_CANDIDATES]:
        if not isinstance(item,dict) or not item.get("id") or not item.get("game_title"):continue
        if item.get("platform") is not None and str(item['platform'])!=str(platform):continue
        result.append(ScrapeCandidate("thegamesdb",str(item["id"]),_text(item["game_title"]),system,
            year=_year(item.get("release_date")),description=_text(item.get("overview")),media=_tg_media(boxart,item['id'])))
    return result


def _hfsdb(game,credentials,transport,cancelled):
    system,platform=_system(game,_HF);user,password=_required(credentials,"username","password")
    encoded=base64.b64encode((user+":"+password).encode()).decode("ascii")
    token=_request(transport,"https://db.hfsplay.fr/api/v1/auth/token",method="POST",headers={"Authorization":"Basic "+encoded,"Content-Type":"application/x-www-form-urlencoded"},data=urlencode({"username":user,"password":password}).encode(),cancelled=cancelled)
    if not isinstance(token.get("token"),str) or not token['token']:raise AuthError("HFSDB did not authorize this API login.")
    data=_request(transport,"https://db.hfsplay.fr/api/v1/games",params={"search":_title(game),"system":platform,"limit":MAX_CANDIDATES},headers={"Authorization":"Token "+token['token']},cancelled=cancelled)
    entries=data.get("results",[])
    if not isinstance(entries,list):raise ProviderUnavailable("HFSDB returned an unsupported game list.")
    result=[];languages=_languages(game)
    regional_dates={"us":"released_at_US","eu":"released_at_PAL","jp":"released_at_JPN","wor":"released_at_WORLD"}
    date_order=tuple(dict.fromkeys((regional_dates.get(_game_region(game),'released_at_WORLD'),'released_at_WORLD','released_at_US','released_at_PAL','released_at_JPN')))
    for item in entries[:MAX_CANDIDATES]:
        if not isinstance(item,dict):continue
        title=_hfs_localized(item,"name",languages)
        if not item.get("id") or not title:continue
        source_system=item.get("system",{})
        if isinstance(source_system,dict) and source_system.get("id") is not None and str(source_system['id'])!=str(platform):continue
        media={};metadata={}
        for m in item.get("metadata",[]):
            if isinstance(m,dict):metadata[_text(m.get("name"))]=_text(m.get("value"))
        for m in item.get("medias",[]):
            if not isinstance(m,dict):continue
            typ,desc=str(m.get("type","")),str(m.get("description",""))
            if typ=="cover2d" and (not desc or desc=="front"):role="cover"
            else:role={"screenshot":"screenshot","wallpaper":"hero","artwork":"hero","logo":"logo"}.get(typ)
            if role:_media_add(media,role,m.get("file"),"https://db.hfsplay.fr/")
        result.append(ScrapeCandidate("hfsdb",str(item['id']),title,system,
            year=_year(next((item.get(k) for k in date_order if item.get(k)),None)),
            publisher=metadata.get("editor",metadata.get("manufacturer","")),developer=metadata.get("developer",""),genre=metadata.get("genre",""),
            description=_hfs_localized(item,"description",languages),media=media))
    return result


def _igdb(game,credentials,transport,cancelled):
    system,platform=_system(game,_IG);client,secret=_required(credentials,"client_id","client_secret")
    token=_request(transport,"https://id.twitch.tv/oauth2/token",method="POST",headers={"Content-Type":"application/x-www-form-urlencoded"},data=urlencode({"client_id":client,"client_secret":secret,"grant_type":"client_credentials"}).encode(),cancelled=cancelled)
    if not isinstance(token.get("access_token"),str) or not token['access_token']:raise AuthError("IGDB did not authorize this client ID and secret.")
    escaped=_title(game).replace("\\","\\\\").replace('"','\\"')
    query=f'search "{escaped}"; fields id,name,summary,first_release_date,platforms,genres.name,cover.image_id,screenshots.image_id,artworks.image_id,involved_companies.company.name,involved_companies.developer,involved_companies.publisher; where platforms = ({platform}); limit {MAX_CANDIDATES};'
    data=_request(transport,"https://api.igdb.com/v4/games",method="POST",headers={"Client-ID":client,"Authorization":"Bearer "+token['access_token'],"Content-Type":"text/plain"},data=query.encode(),cancelled=cancelled)
    if not isinstance(data,list):raise ProviderUnavailable("IGDB returned an unsupported game list.")
    def image(value):
        image_id=value.get("image_id","") if isinstance(value,dict) else ""
        return "https://images.igdb.com/igdb/image/upload/t_original/"+image_id+".jpg" if re.fullmatch(r"[A-Za-z0-9_]+",str(image_id)) else ""
    result=[]
    for item in data[:MAX_CANDIDATES]:
        if not isinstance(item,dict) or not item.get("id") or not item.get("name"):continue
        if isinstance(item.get("platforms"),list) and platform not in item['platforms']:continue
        media={};_media_add(media,"cover",image(item.get("cover")))
        for role,key in (("hero","artworks"),("screenshot","screenshots")):
            for value in item.get(key,[]):_media_add(media,role,image(value))
        year=None
        try:year=datetime.fromtimestamp(int(item['first_release_date']),timezone.utc).year
        except (KeyError,ValueError,TypeError,OverflowError,OSError):pass
        companies={"publisher":[],"developer":[]}
        for value in item.get("involved_companies",[]):
            if not isinstance(value,dict):continue
            name=_text(value.get("company",{}).get("name"))
            for role in companies:
                if value.get(role) and name:companies[role].append(name)
        result.append(ScrapeCandidate("igdb",str(item['id']),_text(item['name']),system,year=year,
            publisher=", ".join(companies['publisher']),developer=", ".join(companies['developer']),
            genre=", ".join(_text(v.get('name')) for v in item.get('genres',[]) if isinstance(v,dict)),description=_text(item.get('summary')),media=media))
    return result


def _arcadedb(game,credentials,transport,cancelled):
    system,_=_system(game,{"arcade":True,"neo_geo":True})
    # This API looks up a MAME set identifier, not a human game title.
    name=str(game.get("rom_name") or game.get("source_name") or _title(game))
    name=re.sub(r"\.[A-Za-z0-9]{1,6}$","",name.rsplit("/",1)[-1].rsplit("\\",1)[-1])
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}",name):raise ScraperError("ArcadeDB needs the game's arcade ROM-set name, such as mslug.")
    data=_request(transport,"https://adb.arcadeitalia.net/service_scraper.php",params={"ajax":"query_mame","lang":"en","use_parent":1,"game_name":name},cancelled=cancelled)
    if int(data.get("release",0))<4 or not isinstance(data.get("result"),list):raise ProviderUnavailable("ArcadeDB returned an unsupported response.")
    result=[]
    for item in data['result'][:MAX_CANDIDATES]:
        if not isinstance(item,dict) or not item.get("title"):continue
        media={}
        for role,key in (("cover","url_image_flyer"),("screenshot","url_image_ingame"),("logo","url_image_marquee")):_media_add(media,role,item.get(key))
        result.append(ScrapeCandidate("arcadedb",_text(item.get("game_name",name)),_text(item['title']),system,year=_year(item.get('year')),
            publisher=_text(item.get('manufacturer')),developer=_text(item.get('manufacturer')),genre=_text(item.get('genre')),description=_text(item.get('history')),media=media))
    return result


_TREE_CACHE={}
_TREE_LOCK=threading.Lock()


def _title_key(value):
    value=unicodedata.normalize("NFKC",value).casefold()
    romans={"i":"1","ii":"2","iii":"3","iv":"4","v":"5","vi":"6","vii":"7","viii":"8","ix":"9","x":"10"}
    value=re.sub(r"\b(i|ii|iii|iv|v|vi|vii|viii|ix|x)$",lambda m:romans[m[0]],value)
    return re.sub(r"[^\w]","",value)


def _libretro(game,credentials,transport,cancelled):
    system,repo=_system(game,_LR);title=_title(game);wanted=_title_key(title)
    with _TREE_LOCK:cached=_TREE_CACHE.get(repo)
    if transport is _transport and cached and cached[0]>time.monotonic()-1800:data=cached[1]
    else:
        data=_request(transport,f"https://api.github.com/repos/libretro-thumbnails/{repo}/git/trees/master",params={"recursive":1},cancelled=cancelled)
        if data.get("truncated"):raise ProviderUnavailable("This artwork index is too large for a complete lookup. Choose another provider.")
        if not isinstance(data.get("tree"),list):raise ProviderUnavailable("The community artwork index is unavailable.")
        if transport is _transport:
            with _TREE_LOCK:_TREE_CACHE[repo]=(time.monotonic(),data)
    entries={};expected=_game_region(game)
    for item in data['tree']:
        _cancel(cancelled)
        path=item.get("path","") if isinstance(item,dict) else ""
        match=re.fullmatch(r"(Named_Boxarts|Named_Snaps|Named_Titles)/([^/]+)\.png",path)
        if not match:continue
        stem=match[2];name=re.sub(r"\s*\([^)]*\)\s*"," ",stem).strip()
        # Preserve meaningful edition/revision tags instead of matching another release.
        tags=re.findall(r"\(([^)]*)\)",stem)
        country=next((tag for tag in tags if _region(tag)),"")
        remaining=[tag for tag in tags if tag!=country and not all(p.casefold().strip() in {"en","fr","de","es","it","sv","nl","pt","ja","ko","zh"} for p in tag.split(','))]
        if remaining or _title_key(name)!=wanted:continue
        region=_region(country)
        if expected and region and region!=expected:continue
        key=(stem,region);entry=entries.setdefault(key,{"name":stem,"region":country,"media":{}})
        role="cover" if match[1]=="Named_Boxarts" else "screenshot"
        _media_add(entry['media'],role,f"https://raw.githubusercontent.com/libretro-thumbnails/{repo}/master/"+quote(path,safe='/'))
    return [ScrapeCandidate('libretro',entry['name'],title,system,entry['region'],media=entry['media']) for entry in list(entries.values())[:MAX_CANDIDATES]]


_SEARCH={"screenscraper":_screenscraper,"thegamesdb":_thegamesdb,"hfsdb":_hfsdb,"igdb":_igdb,"arcadedb":_arcadedb,"libretro":_libretro}


def search(provider,game,credentials=None,*,cancelled=None,transport=None):
    """Return bounded provider choices. Never select a fuzzy match automatically."""
    if provider not in _SEARCH:raise ScraperError("Choose an available artwork provider.")
    _cancel(cancelled)
    try:return _SEARCH[provider](game,credentials or {},transport or _transport,cancelled)
    except ScraperError:raise
    except Exception:raise ProviderUnavailable("The artwork service returned an unsupported response.",provider=provider) from None


def details(candidate,credentials=None,*,cancelled=None,transport=None):
    """Load additional TheGamesDB image roles only after a game is selected."""
    _cancel(cancelled)
    if candidate.provider!="thegamesdb":return candidate
    key,=_required(credentials or {},"api_key")
    data=_request(transport or _transport,"https://api.thegamesdb.net/v1/Games/Images",params={"apikey":key,"games_id":candidate.id},cancelled=cancelled)
    media=dict(candidate.media)
    for role,url in _tg_media(data.get('data',{}),candidate.id).items():media.setdefault(role,url)
    return replace(candidate,media=media)
