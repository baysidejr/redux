"""Artwork service acceptance with isolated libraries and zero network traffic."""
from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock,patch
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request,HTTPRedirectHandler

from PIL import Image,ImageDraw

from redux.library import ARTWORK_FIELDS,ImportCancelled,Library
from redux.scraper_providers import AuthError,QuotaError,ScrapeCandidate,ScrapeCancelled
from redux.scraping import ScraperService,download_image,exact_candidate,_ImageRedirect


def bitmap(color="red",size=(96,160),format="PNG"):
    output=BytesIO();Image.new("RGB",size,color).save(output,format=format);return output.getvalue()


class FakeResponse(BytesIO):
    def __init__(self,data,url="https://art.example/image.png",headers=None):
        super().__init__(data);self.url=url;self.headers=headers or {}
    def geturl(self):return self.url


class TestSettings:
    def __init__(self,**values):
        self.values=dict(provider="screenscraper",region="us",language="en",overwrite=False,
                         roles=list(ARTWORK_FIELDS),tile_style="mixed",automatic=False)
        self.values.update(values)
        self.credentials=Mock(return_value={"username":"fixture","password":"synthetic-secret"})
    def load(self):return dict(self.values,roles=list(self.values["roles"]))


class ScrapingServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.library=Library(self.root/"library")
        media=self.root/"Game.nes";media.write_bytes(b"original media fixture")
        self.game=self.library.import_game(media,system="saturn",title="Game")[0]
        self.library.update_metadata(self.game["id"],region="USA",year=1996)
        self.settings=TestSettings()
        self.searcher=Mock();self.detailer=Mock(side_effect=lambda candidate,*args,**kwargs:candidate)
        self.downloaded=[]
        self.downloader=Mock(side_effect=self.download)

    def candidate(self,**changes):
        values=dict(provider="screenscraper",id="42",title="Game",system="saturn",region="us",year=1996,
                    publisher="Remote publisher",developer="Remote developer",genre="Sport",description="Remote description",
                    media={role:"https://art.example/"+role+".png?account=synthetic-secret" for role in ARTWORK_FIELDS})
        values.update(changes);return ScrapeCandidate(**values)

    def service(self,**kwargs):
        return ScraperService(self.library,self.settings,search=self.searcher,
                              details=self.detailer,downloader=kwargs.get("downloader",self.downloader))

    def local_image(self,name,color="red",size=(96,160)):
        path=self.root/name;path.write_bytes(bitmap(color,size));return path

    def download(self,url,path,**kwargs):
        role=Path(urlsplit(url).path).stem
        color={"cover":"red","tile":"blue","hero":"green","screenshot":"yellow","logo":"purple"}.get(role,"gray")
        path.write_bytes(bitmap(color,(320,224) if role in {"tile","hero","screenshot"} else (96,160)))
        self.downloaded.append(role)
        return {"source_url":url,"width":320,"height":224,"password":"synthetic-secret"}

    def test_search_uses_selected_account_and_keeps_query_changes_out_of_library(self):
        self.searcher.return_value=[self.candidate()]
        found=self.service().search(self.game["id"],query="Different lookup")
        self.assertEqual(len(found),1)
        provider,game,credentials=self.searcher.call_args.args
        self.assertEqual(provider,"screenscraper")
        self.assertEqual((game["title"],game["scrape_region"],game["scrape_language"]),("Different lookup","us","en"))
        self.assertEqual(credentials["password"],"synthetic-secret")
        self.assertEqual(self.library.game(self.game["id"])["title"],"Game")

    def test_apply_downloads_five_roles_portably_and_saves_public_provenance_only(self):
        result=self.service().apply(self.game["id"],self.candidate())
        self.assertEqual(set(result["roles"]),set(ARTWORK_FIELDS))
        game=self.library.game(self.game["id"])
        for role,field in ARTWORK_FIELDS.items():
            self.assertFalse(Path(game[field]).is_absolute())
            with Image.open(self.library.artwork_for(game,role)) as image:self.assertEqual(image.format,"PNG")
        records=self.library.artwork_provenance(game["id"])
        self.assertEqual(set(records),set(ARTWORK_FIELDS))
        self.assertNotIn("synthetic-secret",json.dumps(records))
        self.assertTrue(all(record["provider_game_id"]=="42" for record in records.values()))
        self.assertFalse(list((self.library.root/"cache"/"scraper-staging").glob("art-*")))
        self.assertNotIn(b"synthetic-secret",self.library.db.read_bytes())

    def test_preserves_user_cover_and_metadata_while_filling_missing_art(self):
        initial=self.library.set_artwork(self.game["id"],self.local_image("manual.png"))
        self.library.update_metadata(initial["id"],publisher="My publisher",description="My description")
        self.settings.values["roles"]=["cover","hero"]
        result=self.service().apply(initial["id"],self.candidate())
        self.assertEqual(result["roles"],["hero"])
        self.assertEqual(result["game"]["artwork_path"],initial["artwork_path"])
        self.assertEqual(result["game"]["publisher"],"My publisher")
        self.assertEqual(result["game"]["description"],"My description")
        self.assertNotIn("cover",self.downloaded)

    def test_explicit_overwrite_replaces_existing_cover(self):
        initial=self.library.set_artwork(self.game["id"],self.local_image("manual.png","black"))
        self.settings.values.update(roles=["cover"],overwrite=True)
        result=self.service().apply(initial["id"],self.candidate())
        self.assertEqual(result["roles"],["cover"])
        self.assertNotEqual(result["game"]["artwork_path"],initial["artwork_path"])

    def test_repeat_scrape_keeps_available_art_when_provider_has_no_other_requested_roles(self):
        self.settings.values["provider"]="libretro"
        before=self.library.set_artworks(self.game["id"],{
            "cover":self.local_image("cover.png"),
            "tile":self.local_image("tile.png","blue",(320,224)),
            "screenshot":self.local_image("screenshot.png","green",(320,240))})
        candidate=self.candidate(provider="libretro",media={
            "cover":"https://art.example/cover.png","screenshot":"https://art.example/screenshot.png"})
        result=self.service().apply(self.game["id"],candidate)
        self.assertEqual(result["roles"],[])
        self.assertTrue(any("kept" in note.casefold() for note in result["notes"]))
        for role in ("cover","tile","screenshot"):
            self.assertEqual(result["game"][ARTWORK_FIELDS[role]],before[ARTWORK_FIELDS[role]])
        self.assertFalse(result["game"]["hero_path"] or result["game"]["logo_path"])
        self.searcher.return_value=[candidate]
        batch=self.service().batch([self.game["id"]])
        self.assertEqual(batch.skipped,[self.game["id"]])
        self.assertFalse(batch.errors or batch.updated)
        self.downloader.assert_not_called()

    def test_match_with_no_available_or_existing_images_reports_failure(self):
        candidate=self.candidate(media={})
        with self.assertRaises(OSError):self.service().apply(self.game["id"],candidate)
        self.searcher.return_value=[candidate]
        batch=self.service().batch([self.game["id"]])
        self.assertEqual(len(batch.errors),1)
        self.assertFalse(batch.updated or batch.skipped)
        self.assertFalse(any(self.library.game(self.game["id"])[field] for field in ARTWORK_FIELDS.values()))
        self.downloader.assert_not_called()

    def test_mixed_tile_is_generated_without_cropping_portrait_cover(self):
        self.settings.values["roles"]=["cover","tile","screenshot","logo"]
        candidate=self.candidate(media={role:"https://art.example/"+role+".png" for role in ("cover","screenshot","logo")})
        def download(url,path,**kwargs):
            if "cover" not in url:return self.download(url,path,**kwargs)
            image=Image.new("RGB",(100,300),"black");draw=ImageDraw.Draw(image)
            draw.rectangle((0,0,99,18),fill="red");draw.rectangle((0,281,99,299),fill="blue")
            image.save(path);return {"source_url":url}
        result=self.service(downloader=download).apply(self.game["id"],candidate)
        self.assertIn("tile",result["roles"])
        with Image.open(self.library.artwork_for(result["game"],"tile")) as tile:
            self.assertEqual(tile.size,(640,448))
            colors=set(tile.convert("RGB").get_flattened_data())
            self.assertIn((255,0,0),colors);self.assertIn((0,0,255),colors)
            self.assertIn((128,0,128),colors,"A downloaded logo should appear in the generated tile")
        self.assertIn("composed",self.library.artwork_provenance(self.game["id"])["tile"]["attribution"])

    def test_tile_only_can_use_a_hero_only_provider_match(self):
        self.settings.values["roles"]=["tile"]
        result=self.service().apply(self.game["id"],self.candidate(media={"hero":"https://art.example/hero.png"}))
        self.assertEqual(result["roles"],["tile"])
        self.assertTrue(self.library.artwork_for(result["game"],"tile").is_file())
        self.assertEqual(result["game"]["hero_path"],"","Only selected roles should be published")

    def test_shared_provider_url_is_downloaded_once_for_multiple_roles(self):
        self.settings.values["roles"]=["hero","screenshot"]
        result=self.service().apply(self.game["id"],self.candidate(media={
            "hero":"https://art.example/shared.png","screenshot":"https://art.example/shared.png"}))
        self.assertEqual(self.downloader.call_count,1)
        self.assertEqual(set(result["roles"]),{"hero","screenshot"})
        self.assertEqual(result["game"]["hero_path"],result["game"]["screenshot_path"])

    def test_concurrent_manual_selection_is_not_reported_as_scraper_update(self):
        self.settings.values["roles"]=["cover"]
        manual=self.local_image("new-manual.png","black")
        def select_cover_during_download(url,path,**kwargs):
            info=self.download(url,path,**kwargs)
            self.library.set_artwork(self.game["id"],manual)
            return info
        result=self.service(downloader=select_cover_during_download).apply(self.game["id"],self.candidate())
        self.assertEqual(result["roles"],[])
        self.assertEqual(self.library.artwork_for(result["game"]).read_bytes(),manual.read_bytes())

    def test_batch_repairs_missing_managed_art_but_preserves_external_offline_refs(self):
        old=self.library.set_artwork(self.game["id"],self.local_image("old.png","black"))
        self.library.artwork_for(old).unlink()
        self.settings.values["roles"]=["cover"];self.searcher.return_value=[self.candidate()]
        result=self.service().batch([self.game["id"]])
        self.assertEqual(result.updated,[self.game["id"]])
        self.assertTrue(self.library.artwork_for(self.library.game(self.game["id"])).is_file())
        external=self.root/"unplugged-drive"/"cover.png"
        self.library.update_metadata(self.game["id"],artwork_path=str(external))
        self.searcher.reset_mock()
        result=self.service().batch([self.game["id"]])
        self.assertEqual(result.skipped,[self.game["id"]])
        self.assertEqual(self.library.game(self.game["id"])["artwork_path"],str(external))

    def test_wrong_system_or_ambiguous_or_wrong_region_never_auto_applies(self):
        self.settings.values["roles"]=["cover"]
        cases=[[self.candidate(system="psx")],[self.candidate(region="jp")],
               [self.candidate(),self.candidate(id="other")],[self.candidate(title="Game 2")]]
        for candidates in cases:
            with self.subTest(candidates=candidates):
                self.searcher.return_value=candidates
                result=self.service().batch([self.game["id"]])
                self.assertEqual(result.unmatched,[self.game["id"]])
                self.assertFalse(result.updated)
        self.detailer.assert_not_called();self.downloader.assert_not_called()
        self.assertEqual(self.library.game(self.game["id"])["artwork_path"],"")

    def test_exact_match_handles_known_region_tags_but_not_edition_or_year_changes(self):
        game=dict(self.game,title="Game (USA) (En,Fr)",region="North America",year=1996)
        candidate=self.candidate()
        self.assertIs(exact_candidate(game,[candidate]),candidate)
        self.assertIsNone(exact_candidate(game,[replace(candidate,title="Game Special Edition")]))
        self.assertIsNone(exact_candidate(game,[replace(candidate,year=1997)]))
        self.assertIs(exact_candidate(game,[candidate,candidate]),candidate)

    def test_batch_prefers_identified_region_over_regionless_and_foreign_duplicates(self):
        self.library.update_metadata(self.game['id'],title='Game (USA) (Disc 1) (Rev 1)',region='')
        self.searcher.return_value=[self.candidate(id='legacy',region=''),self.candidate(id='jp',region='jp'),
                                   self.candidate(id='eu',region='eu'),self.candidate(id='br',region='country:7'),
                                   self.candidate(id='us',region='us')]
        result=self.service().batch([self.game['id']])
        self.assertEqual(result.updated,[self.game['id']])
        self.assertFalse(result.unmatched or result.errors)
        self.assertEqual(self.detailer.call_args.args[0].id,'us')
        game=self.library.game(self.game['id'])
        self.assertEqual(game['title'],'Game (USA) (Disc 1) (Rev 1)')
        self.assertTrue(all(self.library.artwork_for(game,role).is_file() for role in ARTWORK_FIELDS))
        # Reopening the library must retain every applied role, not just downloads.
        reopened=Library(self.library.root)
        self.assertTrue(all(reopened.artwork_for(reopened.game(game['id']),role).is_file() for role in ARTWORK_FIELDS))

    def test_batch_keeps_truly_ambiguous_releases_and_respects_title_region(self):
        game=dict(self.game,title='Game (Japan) (Disc 1)',region='')
        us=self.candidate(id='us',region='us');jp=self.candidate(id='jp',region='jp')
        self.assertIs(exact_candidate(game,[us,jp],preferred_region='us'),jp)
        self.assertIsNone(exact_candidate(game,[jp,replace(jp,id='jp-other')]))
        self.assertIsNone(exact_candidate(game,[replace(jp,title='Game (Special Edition)')]))
        self.assertEqual(exact_candidate(dict(game,title='Game III (Japan)'),[replace(jp,title='Game 3')]).title,'Game 3')

    def test_changed_detail_identity_is_rejected_before_any_download(self):
        original=self.candidate()
        for changed in (replace(original,system="psx"),replace(original,id="other"),replace(original,provider="igdb")):
            with self.subTest(changed=changed):
                self.detailer.side_effect=None;self.detailer.return_value=changed
                with self.assertRaises(ValueError):self.service().apply(self.game["id"],original)
        self.downloader.assert_not_called()
        self.assertEqual(self.library.game(self.game["id"])["artwork_path"],"")

    def test_cancellation_after_download_keeps_entire_game_art_unchanged(self):
        cancelled=threading.Event();self.settings.values["roles"]=["cover","hero"]
        self.searcher.return_value=[self.candidate()]
        def cancel_during_download(*args,**kwargs):
            info=self.download(*args,**kwargs);cancelled.set();return info
        result=self.service(downloader=cancel_during_download).batch([self.game["id"]],cancelled=cancelled)
        self.assertTrue(result.cancelled);self.assertFalse(result.updated)
        game=self.library.game(self.game["id"])
        self.assertFalse(game["artwork_path"] or game["hero_path"])
        self.assertEqual(self.library.artwork_provenance(game["id"]),{})
        self.assertFalse(list((self.library.root/"cache"/"scraper-staging").glob("art-*")))

    def test_provider_cancellation_propagates_without_becoming_a_failed_game(self):
        self.searcher.side_effect=ScrapeCancelled("Artwork lookup cancelled.")
        result=self.service().batch([self.game["id"]])
        self.assertTrue(result.cancelled);self.assertFalse(result.errors)

    def test_provider_detail_and_download_cancellation_preserve_art_and_stop_batch(self):
        self.searcher.return_value=[self.candidate()]
        self.detailer.side_effect=ScrapeCancelled("Artwork details cancelled.")
        result=self.service().batch([self.game["id"]])
        self.assertTrue(result.cancelled);self.assertFalse(result.errors)
        self.detailer.side_effect=lambda candidate,*args,**kwargs:candidate
        self.downloader.side_effect=ScrapeCancelled("Artwork image cancelled.")
        result=self.service().batch([self.game["id"]])
        self.assertTrue(result.cancelled);self.assertFalse(result.errors)
        self.assertEqual(self.library.artwork_provenance(self.game["id"]),{})

    def test_failed_image_error_is_sanitized_and_does_not_publish_art(self):
        self.settings.values["roles"]=["cover"];self.searcher.return_value=[self.candidate()]
        self.downloader.side_effect=OSError("https://art.example/?password=synthetic-secret")
        result=self.service().batch([self.game["id"]])
        self.assertFalse(result.updated);self.assertEqual(len(result.errors),1)
        self.assertNotIn("synthetic-secret",str(result.errors))
        self.assertEqual(self.library.game(self.game["id"])["artwork_path"],"")

    def test_account_or_quota_error_stops_batch_without_leaking_server_details(self):
        second=self.root/"Other.nes";second.write_bytes(b"other fixture")
        other=self.library.import_game(second,system="saturn")[0]
        for kind in (AuthError,QuotaError):
            with self.subTest(kind=kind):
                self.searcher.reset_mock();self.searcher.side_effect=kind("https://service/?password=synthetic-secret")
                result=self.service().batch([self.game["id"],other["id"]])
                self.assertEqual(self.searcher.call_count,1)
                self.assertEqual(len(result.errors),1)
                self.assertNotIn("synthetic-secret",str(result.errors))
                self.assertFalse(result.updated)


class ArtworkDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.destination=Path(self.temp.name)/"normalized.png"

    def test_download_decodes_and_normalizes_jpeg_to_png_with_sanitized_metadata(self):
        opener=Mock(return_value=FakeResponse(bitmap("red",(120,180),"JPEG")))
        info=download_image("https://art.example/cover.jpg?password=synthetic-secret",self.destination,opener=opener)
        with Image.open(self.destination) as image:self.assertEqual((image.format,image.size),("PNG",(120,180)))
        self.assertNotIn("synthetic-secret",json.dumps(info))
        self.assertEqual(opener.call_args.kwargs["timeout"],12)

    def test_private_or_non_https_urls_are_rejected_before_transport(self):
        for url in ("http://art.example/a.png","https://localhost/a.png","https://127.0.0.1/a.png",
                    "https://[::1]/a.png","https://169.254.169.254/a.png","https://user:synthetic-secret@art.example/a.png"):
            with self.subTest(url=url):
                opener=Mock()
                with self.assertRaises(ValueError) as error:download_image(url,self.destination,opener=opener)
                opener.assert_not_called();self.assertNotIn("synthetic-secret",str(error.exception))

    def test_download_limit_applies_to_both_declared_and_streamed_bytes(self):
        with patch("redux.scraping.MAX_IMAGE_BYTES",64):
            for response in (FakeResponse(b"",headers={"Content-Length":"65"}),FakeResponse(b"x"*65)):
                with self.subTest(headers=response.headers):
                    with self.assertRaises(OSError):download_image("https://art.example/large.png",self.destination,opener=Mock(return_value=response))
                    self.assertFalse(self.destination.exists())

    def test_bad_or_oversize_images_and_transport_errors_do_not_expose_credentials(self):
        for response in (FakeResponse(b"not a bitmap"),FakeResponse(bitmap(size=(8,8)))):
            with self.subTest(response=response):
                with self.assertRaises(OSError) as error:
                    download_image("https://art.example/image.png?password=synthetic-secret",self.destination,opener=Mock(return_value=response))
                self.assertNotIn("synthetic-secret",str(error.exception));self.assertFalse(self.destination.exists())
        with patch("redux.scraping.MAX_IMAGE_PIXELS",100):
            with self.assertRaises(OSError):download_image("https://art.example/image.png",self.destination,opener=Mock(return_value=FakeResponse(bitmap(size=(16,16)))))
        with self.assertRaises(OSError) as error:
            download_image("https://art.example/image.png",self.destination,opener=Mock(side_effect=URLError("https://art.example/?password=synthetic-secret")))
        self.assertNotIn("synthetic-secret",str(error.exception))

    def test_unsafe_final_redirect_is_rejected_and_cancellation_does_not_request(self):
        with self.assertRaises(OSError):
            download_image("https://art.example/image.png",self.destination,
                           opener=Mock(return_value=FakeResponse(bitmap(),"http://elsewhere.example/image.png")))
        self.assertFalse(self.destination.exists())
        cancelled=threading.Event();cancelled.set();opener=Mock()
        with self.assertRaises(ImportCancelled):download_image("https://art.example/image.png",self.destination,cancelled=cancelled,opener=opener)
        opener.assert_not_called()

    def test_redirect_handler_rejects_unsafe_target_before_following_it(self):
        request=Request("https://art.example/image.png")
        handler=_ImageRedirect()
        with patch.object(HTTPRedirectHandler,"redirect_request",return_value=Mock()) as following:
            for target in ("http://art.example/image.png","https://127.0.0.1/image.png"):
                with self.subTest(target=target):
                    with self.assertRaises(ValueError):handler.redirect_request(request,None,302,"Moved",{},target)
            following.assert_not_called()

    def test_production_downloader_checks_dns_before_opening_a_connection(self):
        from types import SimpleNamespace
        import socket
        for address in ("10.0.0.8","169.254.169.254","::1"):
            with self.subTest(address=address):
                resolved=[(socket.AF_INET,socket.SOCK_STREAM,6,"",(address,443))]
                with patch("redux.scraping.socket.getaddrinfo",return_value=resolved) as resolver, \
                     patch("redux.scraping.build_opener") as factory:
                    with self.assertRaises(OSError):download_image("https://art.example/private.png",self.destination)
                    resolver.assert_called_once()
                    factory.assert_not_called()
        resolved=[(socket.AF_INET,socket.SOCK_STREAM,6,"",("8.8.8.8",443))]
        opener=Mock(return_value=FakeResponse(bitmap()))
        with patch("redux.scraping.socket.getaddrinfo",return_value=resolved) as resolver, \
             patch("redux.scraping.build_opener",return_value=SimpleNamespace(open=opener)) as factory:
            download_image("https://art.example/public.png",self.destination)
            self.assertEqual(resolver.call_args.args[:2],("art.example",443))
            self.assertTrue(factory.call_args.args[0].resolve)
            opener.assert_called_once()

    def test_injected_opener_never_performs_dns_or_constructs_a_real_opener(self):
        opener=Mock(return_value=FakeResponse(bitmap()))
        with patch("redux.scraping.socket.getaddrinfo",side_effect=AssertionError("Unexpected DNS")) as resolver, \
             patch("redux.scraping.build_opener",side_effect=AssertionError("Unexpected transport")) as factory:
            download_image("https://art.example/isolated.png",self.destination,opener=opener)
            resolver.assert_not_called();factory.assert_not_called();opener.assert_called_once()

    def test_authenticated_redirect_cannot_leave_provider_but_screenscraper_family_is_allowed(self):
        request=Request("https://www.screenscraper.fr/api2/mediaJeu.php?ssid=fixture&sspassword=synthetic-secret")
        handler=_ImageRedirect(resolve=False)
        with patch.object(HTTPRedirectHandler,"redirect_request",return_value=Mock()) as following:
            for target in ("https://other.example/image.png","https://screenscraper.fr.attacker.example/image.png"):
                with self.subTest(target=target):
                    with self.assertRaises(ValueError) as error:
                        handler.redirect_request(request,None,302,"Moved",{},target)
                    self.assertNotIn("synthetic-secret",str(error.exception))
            following.assert_not_called()
            target="https://medias.screenscraper.fr/image.png"
            handler.redirect_request(request,None,302,"Moved",{},target)
            following.assert_called_once()


if __name__=="__main__":unittest.main()
