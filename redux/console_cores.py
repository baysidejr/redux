"""Controller-operated setup for optional integrated emulator cores."""
from .core_profiles import PROFILES
from .embedded import HOSTED_DEFAULTS
from .ui_assets import project_root


def recommended_names():
    return tuple(sorted({PROFILES[s].filename.removesuffix('_libretro.dll') for s in HOSTED_DEFAULTS}))


class ConsoleCores:
    def first_core_setup(self):
        if self.busy or self.overlay or self.preferences.get('core_setup_seen'):return
        folder=project_root()/'tools/cores'
        if any((folder/(name+'_libretro.dll')).is_file() for name in recommended_names()):return
        self.menu('Set up supported systems',[
            ('Download recommended emulators',lambda:self.download_cores(recommended_names())),
            ('Set up later',self._defer_core_setup)],message='Redux can download and configure its recommended emulator cores. Internet is needed for setup. Games and BIOS files are not downloaded. PC games can run without emulator setup.')

    def _defer_core_setup(self):
        self.preferences['core_setup_seen']=True
        self._save_preferences()

    def core_setup_menu(self):
        names=recommended_names()
        folder=project_root()/'tools/cores'
        missing=[n for n in names if not (folder/(n+'_libretro.dll')).is_file()]
        items=[('Set up recommended systems'+(f' · {len(missing)} missing' if missing else ' · installed'),lambda:self.download_cores(names))]
        for name in names+('mupen64plus_next','mame'):
            systems=[s for s,p in PROFILES.items() if p.filename==name+'_libretro.dll']
            family=PROFILES[systems[0]].family
            exists=(folder/(name+'_libretro.dll')).is_file()
            items.append((family+(' · installed' if exists else ' · not installed'),lambda n=name:self.core_options(n)))
        self.menu('Emulator setup',items+[('Back',lambda:None)],message='Choose a core to install, repair or update. Recommended systems use the built-in controller and display defaults. N64 and MAME are optional compatibility-testing backends.')

    def core_options(self,name):
        installed=(project_root()/'tools/cores'/(name+'_libretro.dll')).is_file()
        self.menu(name.replace('_',' ').title(),[
            ('Repair / update' if installed else 'Install',lambda:self.download_cores((name,),repair=installed)),
            ('Back',self.core_setup_menu)],message='Downloads an official Windows x64 core with its upstream license and metadata. Updating may make older save states incompatible. Games, battery saves and BIOS files are kept.')

    def offer_core_download(self,system,retry):
        profile=PROFILES.get(system)
        if profile is None:
            self.notice('Playback unavailable','No integrated core is configured for this system.')
            return
        name=profile.filename.removesuffix('_libretro.dll')
        self.menu('Set up '+profile.family,[
            ('Download and continue',lambda:self.download_cores((name,),retry=retry,system=system)),
            ('Back',lambda:None)],message='This game needs an emulator core. Redux will download it and apply the system defaults. Required BIOS files must be provided separately.')

    def download_cores(self,names,*,repair=False,retry=None,system=None):
        if self.busy:return
        if self.embedded or self.playback and self.playback.active():
            self.notice('Close the game first','Emulator setup is available when no game is running.')
            return
        names=tuple(dict.fromkeys(names))
        folder=project_root()/'tools/cores'
        self.overlay=None
        def work():
            from .core_install import install
            results=[]
            for index,name in enumerate(names):
                self.events.put(('status',f'Setting up emulators · {index+1}/{len(names)} · {name}'))
                results.append(install(name,folder,cancelled=self.cancelled,replace=repair))
            return results
        def done(results):
            self.preferences['core_setup_seen']=True
            if system:self.preferences.setdefault('integrated',{})[system]=True
            self._save_preferences()
            self._configure_detected_backends()
            if retry:retry()
            else:self.menu('Emulators ready',[('Done',lambda:None),('Emulator setup',self.core_setup_menu),
                ('BIOS folder',self.choose_firmware)],message='The selected emulator cores are installed. Redux applies built-in controller and playback settings. Systems requiring BIOS files still need your firmware in the BIOS folder.')
        self.task(work,done,'Downloading emulator cores…',cancellable=True)
