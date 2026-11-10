"""Provider API normalization, credential isolation and conservative matching."""
from dataclasses import asdict
import json
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs,urlsplit

from redux import scraper_providers as providers


class Replies:
    def __init__(self,*values):self.values=list(values);self.calls=[]
    def __call__(self,url,**kwargs):
        self.calls.append((url,kwargs))
        value=self.values.pop(0)
        if isinstance(value,Exception):raise value
        return value


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.game=dict(title="World Series Baseball II (USA)",system="saturn",region="North America")
        self.ss=dict(username="test+name",password="test&password?",developer_id="own-dev",developer_password="own-secret")

    def test_service_menu_has_batocera_choices_and_separate_no_account_fallback(self):
        info=providers.provider_info()
        self.assertEqual([i['id'] for i in info],['screenscraper','thegamesdb','hfsdb','igdb','arcadedb','libretro'])
        self.assertEqual(info[-1]['credentials'],[])
        self.assertNotIn('saturn',info[-2]['systems'])
        self.assertIn('developer_password',{i['key'] for i in info[0]['credentials']})
        info[0]['credentials'].clear()
        self.assertTrue(providers.provider_info()[0]['credentials'])

    def test_missing_credentials_fail_without_network(self):
        for name in ('screenscraper','thegamesdb','hfsdb','igdb'):
            with self.subTest(provider=name):
                request=Replies()
                with self.assertRaises(providers.AuthError):providers.search(name,self.game,{},transport=request)
                self.assertFalse(request.calls)

    def test_credentials_are_encoded_and_exact_platform_is_requested(self):
        request=Replies({'response':{'jeux':[]}})
        self.assertEqual(providers.search('screenscraper',self.game,self.ss,transport=request),[])
        url,options=request.calls[0];query=parse_qs(urlsplit(url).query)
        self.assertEqual(query['ssid'],['test+name'])
        self.assertEqual(query['sspassword'],['test&password?'])
        self.assertEqual(query['systemeid'],['22'])
        self.assertEqual(query['recherche'],['World Series Baseball II'])
        self.assertEqual(urlsplit(url).scheme,'https')
        self.assertNotIn('Batocera',url)

    def test_screenscraper_normalizes_localized_names_and_distinct_media_roles(self):
        entry={'id':88,'systeme':{'id':22},'noms':[{'region':'jp','text':'Japanese name'},{'region':'us','text':'World Series Baseball II'}],
               'dates':[{'region':'us','text':'1996-08-01'}], 'editeur':{'text':'Sega'},'developpeur':{'text':'BlueSky'},
               'synopsis':[{'langue':'fr','text':'FR'},{'langue':'en','text':'English description'}],
               'genres':[{'noms':[{'langue':'en','text':'Sports'}]}],
               'medias':[{'type':'box-2D','region':'jp','url':'https://media.invalid/jp.png'},
                         {'type':'box-2D','region':'us','url':'https://media.invalid/us.png'},
                         {'type':'ss','url':'https://media.invalid/snap.png'},
                         {'type':'mixrbv2','url':'https://media.invalid/mix.png'},
                         {'type':'fanart','url':'https://media.invalid/hero.png'},
                         {'type':'wheel-hd','url':'https://media.invalid/logo.png'}]}
        request=Replies({'response':{'jeux':[entry,{**entry,'id':89,'systeme':{'id':57}}]}})
        result=providers.search('screenscraper',self.game,self.ss,transport=request)
        self.assertEqual(len(result),1)
        c=result[0]
        self.assertEqual((c.title,c.year,c.publisher,c.developer,c.genre,c.description),('World Series Baseball II',1996,'Sega','BlueSky','Sports','English description'))
        self.assertEqual(c.media,{'cover':'https://media.invalid/us.png','tile':'https://media.invalid/mix.png','hero':'https://media.invalid/hero.png','screenshot':'https://media.invalid/snap.png','logo':'https://media.invalid/logo.png'})

    def test_screenscraper_documented_named_media_groups_work(self):
        entry={'id':3,'noms':[{'region':'us','text':'Game'}],'medias':{'media_boxs2d':{'media_box2d_us':'https://media.invalid/box.png','media_box2d_us_md5':'ignored'},'media_screenshot':'https://media.invalid/snap.png'}}
        result=providers.search('screenscraper',self.game,self.ss,transport=Replies({'response':{'jeux':[entry]}}))
        self.assertEqual(result[0].media,{'cover':'https://media.invalid/box.png','screenshot':'https://media.invalid/snap.png'})

    def test_scraper_region_is_fallback_and_requested_language_selects_supplied_text(self):
        entry={'id':3,'noms':[{'region':'us','text':'US name'},{'region':'eu','text':'EU name'}],
               'synopsis':[{'langue':'en','text':'English'},{'langue':'de','text':'Deutsch'}],
               'genres':[{'noms':{'nom_en':'Sports','nom_de':'Sport'}}],
               'medias':[{'type':'box-2D','region':'us','url':'https://media.invalid/us.png'},{'type':'box-2D','region':'eu','url':'https://media.invalid/eu.png'}]}
        game={**self.game,'region':'','scrape_region':'eu','scrape_language':'de'}
        c=providers.search('screenscraper',game,self.ss,transport=Replies({'response':{'jeux':[entry]}}))[0]
        self.assertEqual((c.title,c.region,c.description,c.genre),('EU name','eu','Deutsch','Sport'))
        self.assertEqual(c.media['cover'],'https://media.invalid/eu.png')
        game['region']='North America'
        c=providers.search('screenscraper',game,self.ss,transport=Replies({'response':{'jeux':[entry]}}))[0]
        self.assertEqual((c.title,c.region),('US name','us'))

    def test_screenscraper_japanese_alias_and_missing_translation_fall_back(self):
        entry={'id':3,'noms':[{'text':'Game'}],'synopsis':[{'langue':'en','text':'English'},{'langue':'jp','text':'Japanese'}]}
        for language,wanted in (('ja','Japanese'),('pt','English')):
            with self.subTest(language=language):
                c=providers.search('screenscraper',{**self.game,'scrape_language':language},self.ss,transport=Replies({'response':{'jeux':[entry]}}))[0]
                self.assertEqual(c.description,wanted)

    def test_explicit_region_never_silently_uses_foreign_cover_but_keeps_world_media(self):
        entry={'id':3,'noms':[{'region':'us','text':'USA title'},{'region':'jp','text':'Japanese title'}],
               'medias':[{'type':'box-2D','region':'jp','url':'https://media.invalid/jp-cover.png'},
                         {'type':'mixrbv2','region':'eu','url':'https://media.invalid/eu-tile.png'},
                         {'type':'fanart','region':'wor','url':'https://media.invalid/world-hero.png'},
                         {'type':'ss','region':'','url':'https://media.invalid/regionless-snap.png'}]}
        request=lambda:Replies({'response':{'jeux':[entry]}})
        c=providers.search('screenscraper',self.game,self.ss,transport=request())[0]
        self.assertEqual(c.region,'us')
        self.assertEqual(set(c.media),{'hero','screenshot'})
        unknown={**self.game,'region':'','scrape_region':''}
        c=providers.search('screenscraper',unknown,self.ss,transport=request())[0]
        self.assertIn('cover',c.media)

    def test_screenscraper_documented_statuses_distinguish_miss_quota_and_access(self):
        request=Replies({'code':404,'message':'Game not found'})
        self.assertEqual(providers.search('screenscraper',self.game,self.ss,transport=request),[])
        for status,kind,words in ((401,providers.ProviderUnavailable,'account'),(403,providers.AuthError,'credentials'),
                                  (423,providers.ProviderUnavailable,'temporarily closed'),(426,providers.ProviderUnavailable,'client version'),
                                  (429,providers.QuotaError,'limit'),(430,providers.QuotaError,'daily'),(431,providers.QuotaError,'unrecognized')):
            with self.subTest(status=status),self.assertRaises(kind) as raised:
                providers.search('screenscraper',self.game,self.ss,transport=Replies({'code':status,'message':'password=fixture-secret'}))
            self.assertIn(words,str(raised.exception))
            self.assertNotIn('fixture-secret',str(raised.exception))

    def test_thegamesdb_search_and_selected_game_details_keep_roles_separate(self):
        search={'code':200,'data':{'games':[{'id':42,'game_title':'World Series Baseball II','platform':17,'release_date':'1996-01-01','overview':'Description'},
                                              {'id':43,'game_title':'Wrong platform','platform':10}]},
                'include':{'boxart':{'base_url':{'original':'https://cdn.thegamesdb.net/images/original/'},'data':{'42':[{'type':'boxart','side':'front','filename':'boxart/front/42.jpg'}]}}}}
        images={'data':{'base_url':{'original':'https://cdn.thegamesdb.net/images/original/'},'data':{'42':[{'type':'screenshot','filename':'screenshots/42.jpg'},{'type':'fanart','filename':'fanart/42.jpg'},{'type':'clearlogo','filename':'clearlogo/42.png'}]}}}
        request=Replies(search,images)
        result=providers.search('thegamesdb',self.game,{'api_key':'own-key'},transport=request)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0].media,{'cover':'https://cdn.thegamesdb.net/images/original/boxart/front/42.jpg'})
        detailed=providers.details(result[0],{'api_key':'own-key'},transport=request)
        self.assertEqual(set(detailed.media),{'cover','screenshot','hero','logo'})
        self.assertNotIn('tile',detailed.media)
        self.assertEqual(parse_qs(urlsplit(request.calls[0][0]).query)['filter[platform]'],['17'])

    def test_thegamesdb_pc_search_uses_pc_platform_and_rejects_console_results(self):
        title='Clair Obscur: Expedition 33'
        request=Replies({'code':200,'data':{'games':[
            {'id':501,'game_title':title,'platform':1,'release_date':'2025-04-24'},
            {'id':502,'game_title':title,'platform':10}]}})
        results=providers.search('thegamesdb',{'title':title,'system':'pc'},{'api_key':'fixture-key'},transport=request)
        self.assertEqual([(game.id,game.system,game.title) for game in results],[('501','pc',title)])
        query=parse_qs(urlsplit(request.calls[0][0]).query)
        self.assertEqual(query['filter[platform]'],['1'])
        self.assertEqual(query['name'],[title])
        self.assertIn('pc',next(info for info in providers.provider_info() if info['id']=='thegamesdb')['systems'])

    def test_thegamesdb_live_images_response_shape_supplies_screenshot_and_logo(self):
        candidate=providers.ScrapeCandidate('thegamesdb','131231','Clair Obscur: Expedition 33','pc',
            media={'cover':'https://cdn.thegamesdb.net/images/original/boxart/front/131231.jpg'})
        request=Replies({'data':{'count':2,'base_url':{'original':'https://cdn.thegamesdb.net/images/original/'},
            'images':{'131231':[{'type':'screenshot','filename':'screenshots/131231-1.jpg'},
                {'type':'clearlogo','filename':'clearlogo/131231.png'}]}}})
        result=providers.details(candidate,{'api_key':'fixture-key'},transport=request)
        self.assertEqual(set(result.media),{'cover','screenshot','logo'})
        self.assertTrue(result.media['screenshot'].endswith('screenshots/131231-1.jpg'))

    def test_hfs_uses_the_supplied_api_login_and_provider_system(self):
        request=Replies({'token':'fixture-token'},{'count':1,'results':[{'id':12,'system':{'id':82924},'name_en':'Baseball','description_en':'Info',
            'released_at_US':'1996-01-01T00:00:00','metadata':[{'name':'editor','value':'Sega'},{'name':'developer','value':'BlueSky'},{'name':'genre','value':'Sports'}],
            'medias':[{'type':'cover2d','description':'front','file':'/art/cover.png'},{'type':'screenshot','description':'in game','file':'https://db.hfsplay.fr/art/snap.png'},{'type':'logo','file':'/art/logo.png'}]}]})
        c=providers.search('hfsdb',self.game,{'username':'my+api','password':'my&password'},transport=request)[0]
        self.assertEqual(c.media['cover'],'https://db.hfsplay.fr/art/cover.png')
        self.assertEqual(c.publisher,'Sega')
        self.assertEqual(c.year,1996)
        self.assertEqual(parse_qs(request.calls[0][1]['data'].decode())['password'],['my&password'])
        self.assertEqual(request.calls[1][1]['headers']['Authorization'],'Token fixture-token')
        self.assertEqual(parse_qs(urlsplit(request.calls[1][0]).query)['system'],['82924'])

    def test_hfs_requires_actual_token_and_does_not_search_on_failed_auth(self):
        request=Replies({})
        with self.assertRaises(providers.AuthError):providers.search('hfsdb',self.game,{'username':'api','password':'password'},transport=request)
        self.assertEqual(len(request.calls),1)

    def test_hfs_honors_supplied_language_and_regional_date_with_english_fallback(self):
        item={'id':12,'system':{'id':82924},'name_en':'Baseball','name_fr':'Baseball FR','description_en':'English','description_fr':'Francais',
              'released_at_US':'1996-01-01T00:00:00','released_at_PAL':'1997-01-01T00:00:00','medias':[]}
        creds={'username':'api','password':'password'}
        game={**self.game,'region':'','scrape_region':'eu','scrape_language':'fr'}
        c=providers.search('hfsdb',game,creds,transport=Replies({'token':'token'},{'results':[item]}))[0]
        self.assertEqual((c.title,c.description,c.year),('Baseball FR','Francais',1997))
        game['scrape_language']='de'
        c=providers.search('hfsdb',game,creds,transport=Replies({'token':'token'},{'results':[item]}))[0]
        self.assertEqual((c.title,c.description),('Baseball','English'))

    def test_igdb_uses_oauth_post_and_escapes_search_syntax(self):
        game={'title':'A "quoted" game','system':'psx'}
        request=Replies({'access_token':'fixture-bearer'},[{'id':9,'name':'A game','platforms':[7],'summary':'Summary','first_release_date':820454400,
            'cover':{'image_id':'co123'},'screenshots':[{'image_id':'sc123'}],'artworks':[{'image_id':'ar123'}],
            'genres':[{'name':'Adventure'}],'involved_companies':[{'company':{'name':'Studio'},'developer':True,'publisher':False}]}])
        c=providers.search('igdb',game,{'client_id':'my-client','client_secret':'own-secret'},transport=request)[0]
        token_request=request.calls[0][1]
        self.assertEqual(token_request['method'],'POST')
        self.assertEqual(parse_qs(token_request['data'].decode())['client_secret'],['own-secret'])
        query=request.calls[1][1]['data'].decode()
        self.assertIn(r'search "A \"quoted\" game";',query)
        self.assertIn('platforms = (7)',query)
        self.assertEqual(c.developer,'Studio')
        self.assertEqual(c.year,1996)
        self.assertEqual(c.media['cover'],'https://images.igdb.com/igdb/image/upload/t_original/co123.jpg')
        self.assertNotIn('tile',c.media)

    def test_arcadedb_rejects_console_without_request_and_requires_rom_set_name(self):
        request=Replies()
        with self.assertRaises(providers.ScraperError):providers.search('arcadedb',self.game,transport=request)
        with self.assertRaises(providers.ScraperError):providers.search('arcadedb',{'title':'Metal Slug','system':'arcade'},transport=request)
        self.assertFalse(request.calls)

    def test_arcadedb_maps_public_arcade_fields_and_upgrades_known_image_host(self):
        request=Replies({'release':4,'result':[{'game_name':'mslug','title':'Metal Slug','year':'1996','manufacturer':'Nazca','genre':'Shooter','history':'History',
                                                   'url_image_ingame':'http://adb.arcadeitalia.net/snap.png','url_image_flyer':'https://adb.arcadeitalia.net/flyer.png'}]})
        c=providers.search('arcadedb',{'title':'Metal Slug','rom_name':'mslug.zip','system':'arcade'},transport=request)[0]
        self.assertEqual(c.media,{'cover':'https://adb.arcadeitalia.net/flyer.png','screenshot':'https://adb.arcadeitalia.net/snap.png'})
        self.assertEqual(parse_qs(urlsplit(request.calls[0][0]).query)['game_name'],['mslug'])

    def test_libretro_returns_only_real_indexed_files_of_matching_title_region_system(self):
        request=Replies({'tree':[
            {'path':'Named_Boxarts/World Series Baseball II (USA).png'},
            {'path':'Named_Snaps/World Series Baseball II (USA).png'},
            {'path':'Named_Boxarts/World Series Baseball II (Japan).png'},
            {'path':'Named_Boxarts/World Series Baseball II (USA) (Rev 1).png'},
            {'path':'Named_Boxarts/World Series Baseball 98 (USA).png'},
            {'path':'Named_Boxarts/Other Game (USA).png'}]})
        game={**self.game,'title':'World Series BASEBALL 2'}
        result=providers.search('libretro',game,transport=request)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0].region,'USA')
        self.assertEqual(set(result[0].media),{'cover','screenshot'})
        self.assertIn('World%20Series%20Baseball%20II%20%28USA%29.png',result[0].media['cover'])
        self.assertIn('Sega_-_Saturn',request.calls[0][0])

    def test_libretro_no_match_and_truncated_index_are_not_fake_success(self):
        self.assertEqual(providers.search('libretro',self.game,transport=Replies({'tree':[]})),[])
        with self.assertRaises(providers.ProviderUnavailable):providers.search('libretro',self.game,transport=Replies({'truncated':True,'tree':[]}))

    def test_libretro_uses_scraper_region_when_original_region_is_unknown(self):
        tree={'tree':[{'path':'Named_Boxarts/World Series Baseball II (USA).png'},{'path':'Named_Boxarts/World Series Baseball II (Europe).png'}]}
        result=providers.search('libretro',{**self.game,'region':'','scrape_region':'eu'},transport=Replies(tree))
        self.assertEqual([c.region for c in result],['Europe'])

    def test_cancelled_lookup_never_sends_credentials(self):
        cancelled=threading.Event();cancelled.set();request=Replies()
        with self.assertRaises(providers.ScrapeCancelled):providers.search('screenscraper',self.game,self.ss,cancelled=cancelled,transport=request)
        self.assertFalse(request.calls)

    def test_cancel_between_oauth_and_search_does_not_send_second_request(self):
        cancelled=threading.Event()
        def request(url,**kwargs):cancelled.set();return {'access_token':'token'}
        with self.assertRaises(providers.ScrapeCancelled):providers.search('igdb',self.game,{'client_id':'client','client_secret':'secret'},cancelled=cancelled,transport=request)

    def test_raw_transport_exception_and_service_error_never_expose_credentials(self):
        for response in (RuntimeError('https://service.invalid/?password=secret'),{'error':'password secret rejected'}):
            with self.subTest(response_type=type(response).__name__):
                with self.assertRaises(providers.ScraperError) as raised:
                    providers.search('screenscraper',self.game,self.ss,transport=Replies(response))
                self.assertNotIn('secret',str(raised.exception))
                self.assertNotIn('password=',str(raised.exception))

    def test_quota_and_auth_codes_are_typed(self):
        for status,kind in ((401,providers.ProviderUnavailable),(403,providers.AuthError),(429,providers.QuotaError)):
            with self.subTest(status=status),self.assertRaises(kind):
                providers.search('screenscraper',self.game,self.ss,transport=Replies({'code':status,'message':'untrusted server body'}))

    def test_candidate_repr_omits_authenticated_urls_and_provenance_redacts_all_query_values(self):
        url='https://name:password@api.invalid/media/a.png?ssid=person&sspassword=secret&customToken=hidden#fragment'
        candidate=providers.ScrapeCandidate('screenscraper','1','Game','saturn',media={'cover':url})
        self.assertNotIn('secret',repr(candidate));self.assertNotIn('hidden',repr(candidate))
        label=providers.sanitize_url(url)
        self.assertNotIn('secret',label);self.assertNotIn('person',label);self.assertNotIn('hidden',label)
        self.assertNotIn('name:',label);self.assertNotIn('fragment',label)
        self.assertEqual(set(parse_qs(urlsplit(label).query)),{'ssid','sspassword','customToken'})

    def test_unsafe_media_is_not_returned_and_unexpected_shapes_become_sanitized_errors(self):
        entry={'id':3,'noms':[{'text':'Game'}],'medias':[{'type':'box-2D','url':'file:///C:/secret.png'},{'type':'ss','url':'http://untrusted.invalid/ss.png'}]}
        c=providers.search('screenscraper',self.game,self.ss,transport=Replies({'response':{'jeux':[entry]}}))[0]
        self.assertEqual(c.media,{})
        with self.assertRaises(providers.ProviderUnavailable):providers.search('screenscraper',self.game,self.ss,transport=Replies({'response':[]}))


class TransportTests(unittest.TestCase):
    class Response:
        def __init__(self,data,headers=None):self.data=data;self.headers=headers or {}
        def read(self,count):value=self.data[:count];self.data=self.data[count:];return value
        def __enter__(self):return self
        def __exit__(self,*args):pass

    def test_transport_bounds_body_and_rejects_plain_http(self):
        with self.assertRaises(providers.ProviderUnavailable):providers._transport('http://api.invalid/data')
        opener=unittest.mock.Mock()
        opener.open.return_value=self.Response(b'{}',{'Content-Length':str(providers.MAX_JSON_BYTES+1)})
        with patch.object(providers,'build_opener',return_value=opener),self.assertRaises(providers.ProviderUnavailable):providers._transport('https://api.invalid/data')

    def test_transport_parses_json_and_rejects_credential_leaking_plaintext(self):
        opener=unittest.mock.Mock();opener.open.return_value=self.Response(b'{"items":[]}')
        with patch.object(providers,'build_opener',return_value=opener):self.assertEqual(providers._transport('https://api.invalid/data'),{'items':[]})
        opener.open.return_value=self.Response(b'Login rejected password=fixture-secret')
        with patch.object(providers,'build_opener',return_value=opener),self.assertRaises(providers.AuthError) as raised:providers._transport('https://api.invalid/data')
        self.assertNotIn('fixture-secret',str(raised.exception))

    def test_transport_maps_http_status_without_response_body(self):
        for status,kind in ((401,providers.AuthError),(403,providers.AuthError),(429,providers.QuotaError),(503,providers.ProviderUnavailable)):
            opener=unittest.mock.Mock();opener.open.side_effect=HTTPError('https://api.invalid/?password=secret',status,'secret',{},None)
            with self.subTest(status=status),patch.object(providers,'build_opener',return_value=opener),self.assertRaises(kind) as raised:providers._transport('https://api.invalid/data')
            self.assertNotIn('secret',str(raised.exception))

    def test_api_redirect_does_not_forward_auth_to_another_host(self):
        request=providers.Request('https://api.invalid/data',headers={'Authorization':'Bearer secret'})
        redirect=providers._SameOriginRedirect()
        for target in ('http://api.invalid/data','https://other.invalid/data','https://api.invalid:444/data'):
            with self.subTest(target=target),self.assertRaises(providers.ProviderUnavailable):redirect.redirect_request(request,None,302,'Moved',{},target)


if __name__=='__main__':unittest.main()
