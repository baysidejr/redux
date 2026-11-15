"""Controller-operated setup for optional integrated emulator cores, and firmware copied from a drive."""
from pathlib import Path

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
            ('Download recommended emulators',lambda:self.download_cores(recommended_names()+('bigpemu',))),
            ('Set up later',self._defer_core_setup)],message='Redux can download its recommended emulators, including BigPEmu for non-commercial use under its author terms. Internet is needed for setup. Games and BIOS files are not downloaded. PC games can run without emulator setup.')

    def _defer_core_setup(self):
        self.preferences['core_setup_seen']=True
        self._save_preferences()

    def core_setup_menu(self):
        names=recommended_names()
        folder=project_root()/'tools/cores'
        missing=[n for n in names if not (folder/(n+'_libretro.dll')).is_file()]
        from .bigpemu import executable
        if not executable(project_root()).is_file():missing.append('bigpemu')
        items=[('Set up recommended systems'+(f' · {len(missing)} missing' if missing else ' · installed'),lambda:self.download_cores(names+('bigpemu',)))]
        items.append(('BigPEmu - Jaguar / Jaguar CD',self.bigpemu_setup_menu))
        items.append(('Copy BIOS files from a drive',self.copy_firmware_menu))
        for name in names:
            systems=[s for s,p in PROFILES.items() if p.filename==name+'_libretro.dll']
            family=PROFILES[systems[0]].family
            exists=(folder/(name+'_libretro.dll')).is_file()
            items.append((family+(' · installed' if exists else ' · not installed'),lambda n=name:self.core_options(n)))
        self.menu('Emulator setup',items+[('Back',lambda:None)],message='Choose a core to install, repair or update. Every recommended system uses the built-in controller and display defaults.')

    # ------------------------------------------------------------------ firmware from a drive
    def firmware_drives(self):
        """Connected drives that carry a bios folder with known files: [(root, folder, found)]."""
        from .firmware_files import find_firmware_folder, known_files_in
        from .game_cards import list_drives, can_prepare
        result=[]
        for drive in list_drives():
            try:
                if not can_prepare(drive):continue
                folder=find_firmware_folder(drive)
            except OSError:folder=None
            if folder:result.append((str(drive),folder,known_files_in(folder)))
        return result

    def copy_firmware_menu(self):
        """Pick a connected drive whose bios folder Redux can copy from."""
        from .firmware_files import describe
        drives=self.firmware_drives()
        if not drives:
            self.notice('No BIOS files found','Connect a drive with a bios folder at its root (or inside Redux Games) holding the files your emulators need, such as bios_CD_U.bin for Sega CD or scph5501.bin for PlayStation.')
            return
        items=[(f"{root.rstrip(chr(92)+'/')} · {len(found)} file{'' if len(found)==1 else 's'} · {describe(found)}",lambda folder=folder:self.copy_firmware_from(folder)) for root,folder,found in drives]
        self.menu('Copy BIOS files from a drive',items+[('Cancel',lambda:None)],message='Known firmware files are copied into '+str(self.firmware_directory())+'. Files already there are never replaced.')

    def offer_firmware(self,root,folder,found):
        """The one question for a drive that arrived with BIOS files on it."""
        from .firmware_files import describe
        if not Path(folder).is_dir():return
        letter=str(root).rstrip(chr(92)+'/')
        self.menu(f'BIOS files found on {letter}',[
            (f"Copy {len(found)} file{'' if len(found)==1 else 's'}",lambda:self.copy_firmware_from(folder)),
            ('Not now',lambda:self._ignore_firmware_drive(root))],
            message=f'This drive has firmware for {describe(found)}. Copying puts the files where your emulators look ({self.firmware_directory()}). Files already there are never replaced. Not now leaves the drive alone; System → Emulators & firmware can copy later.')

    def _ignore_firmware_drive(self,root):
        from .game_cards import drive_identity
        identity=drive_identity(root)
        ignored=[item for item in self.preferences.get('firmware_ignored',[]) if str(item).casefold()!=identity.casefold()]+[identity]
        self.preferences['firmware_ignored']=ignored;self._save_preferences()
        self.status.set(f"{str(root).rstrip(chr(92)+'/')} left alone • System → Emulators & firmware can copy its BIOS files later");self.schedule_draw()

    def _firmware_drive_ignored(self,root):
        from .game_cards import drive_identity
        ignored={str(item).casefold() for item in self.preferences.get('firmware_ignored',[])}
        return drive_identity(root).casefold() in ignored or str(root).casefold() in ignored

    def copy_firmware_from(self,folder):
        from .firmware_files import copy_firmware
        destination=self.firmware_directory()
        def work():return copy_firmware(folder,destination,cancelled=self.cancelled)
        def done(result):
            self.notice('BIOS files copied' if result.copied else 'No new BIOS files',result.summary+('\n'+'\n'.join(result.failed[:4]) if result.failed else '')+'\nFolder: '+str(destination))
        self.overlay=None
        self.task(work,done,'Copying BIOS files…',cancellable=True)

    def core_options(self,name):
        installed=(project_root()/'tools/cores'/(name+'_libretro.dll')).is_file()
        firmware_notes={'neocd':' NeoCD uses its built-in HLE when no original BIOS is available; compatibility varies. For original firmware, put your BIOS in the neocd subfolder of the selected BIOS folder (normally bios/neocd). States made with HLE and original BIOS are not interchangeable.',
                        'fbneo':' FinalBurn Neo plays Neo Geo AES/MVS cartridges as zipped ROM sets named by set name (mslug.zip). It needs the neogeo.zip BIOS set in the fbneo subfolder of the selected BIOS folder (normally bios/fbneo) or next to the games; ROM sets must match this FBNeo build. FBNeo is free for non-commercial use.',
                        'opera':' Current Opera includes Opera Boot. Original BIOS such as panafz1.bin or panafz10.bin goes directly in the selected BIOS folder; some games need original firmware.'}
        self.menu(name.replace('_',' ').title(),[
            ('Repair / update' if installed else 'Install',lambda:self.download_cores((name,),repair=installed)),
            ('Back',self.core_setup_menu)],message='Downloads an official Windows x64 core with its upstream license and metadata. Updating may make older save states incompatible. Games, battery saves and BIOS files are kept.'+firmware_notes.get(name,''))

    def bigpemu_setup_menu(self,retry=None):
        self.menu('BigPEmu - Atari Jaguar / Jaguar CD',[
            ('Install / check installation',lambda:self.download_cores(('bigpemu',),retry=retry)),
            ('Back',lambda:None)],message='Downloads BigPEmu 1.221 directly from Rich Whitehouse, verified against the published release. The complete distribution and ReadMe are retained. BigPEmu prohibits private financial gain. Its own menus provide save states and filters. User data stays in its portable UserData folder.')

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
                if name=='bigpemu':
                    from .bigpemu import install as install_bigpemu
                    results.append(install_bigpemu(project_root(),cancelled=self.cancelled))
                else:results.append(install(name,folder,cancelled=self.cancelled,replace=repair))
            return results
        def done(results):
            self.preferences['core_setup_seen']=True
            if system:self.preferences.setdefault('integrated',{})[system]=True
            self._save_preferences()
            self._configure_detected_backends()
            if retry:retry()
            else:self.menu('Emulators ready',[('Done',lambda:None),('Emulator setup',self.core_setup_menu),
                ('BIOS folder',self.choose_firmware)],message='The selected emulators are installed. Redux applies built-in controller and playback settings. Systems requiring BIOS files still need your firmware in the BIOS folder. BigPEmu uses its own save-state and filter menus.')
        self.task(work,done,'Downloading emulator cores…',cancellable=True)
