"""Controller-first shortcuts to already installed PC games."""


class ConsolePCGames:
    # PC games arrive from a game card's PC folder (game_cards.py); only setup of an existing
    # entry happens here.
    def pc_game_setup(self):
        game=self.selected()
        if not game or game["system"]!="pc":return
        path=self.library.path_for(game)
        arguments,folder=self.library.pc_game_config(game)
        def choose():
            self.open_file_browser("Locate installed game EXE",mode="files",extensions={".exe"},
                initialdir=path.parent if path.is_file() else None,
                on_select=lambda executable:self._pc_relocate(game["id"],executable))
        def set_folder(folder):
            self.library.configure_pc_game(game["id"],arguments,folder);self.refresh();self.pc_game_setup()
        self.menu("PC game setup",[("Locate or change game EXE",choose),
            ("Working folder",lambda:self.open_file_browser("Game working folder",mode="folder",initialdir=folder,on_select=set_folder)),
            (f"Launch arguments ({len(arguments)})",self._pc_arguments_menu),
            ("Reset launch options",lambda:self._pc_reset_options(game["id"])),("Back",lambda:None)],
            message=str(path)+"\nWorking folder: "+str(folder)+"\nThe game supplies controller support, graphics options and saves. Windows Xbox mode supplies task switching.")

    def _pc_reset_options(self,game_id):
        self.library.configure_pc_game(game_id,[]);self.refresh();self.pc_game_setup()

    def _pc_arguments_menu(self):
        game=self.selected();arguments,folder=self.library.pc_game_config(game)
        def store(values):
            self.library.configure_pc_game(game["id"],values,folder);self.refresh()
        def edit(index=None):
            def accept(text):
                values=list(arguments)
                if index is None:values.append(text)
                else:values[index]=text
                store(values)
            self.text_input("Launch argument","One argument (no surrounding quotes needed)",accept,
                "" if index is None else arguments[index],credential=True,return_callback=self._pc_arguments_menu)
        items=[(f"{i+1}: {arg}",lambda index=i:edit(index)) for i,arg in enumerate(arguments)]
        if len(arguments)<64:items.append(("Add argument",edit))
        if arguments:items.append(("Remove last argument",lambda:(store(arguments[:-1]),self._pc_arguments_menu())))
        items.append(("Back",self.pc_game_setup))
        self.menu("Launch arguments",items,message="Optional. Most games need none. Each row is passed as one literal argument; no shell commands are run.")
        self.overlay["back_action"]=self.pc_game_setup

    def _pc_relocate(self,game_id,executable):
        def changed(game):
            self.overlay=None;self.refresh();self.open_game(game["id"],tab="manage")
            self.status.set("PC game location updated")
        self.task(lambda:self.library.add_pc_game(executable,game_id=game_id),changed,"Updating game location…")

    def _play_pc_game(self,game):
        path=self.library.path_for(game)
        arguments,folder=self.library.pc_game_config(game)
        session=self.playback.launch(game["id"],path,arguments,path,profile_id="pc_game",title=game["title"],cwd=folder)
        self.sessions[session.session_id]=self.library.start_session(game["id"])
        self.status.set("Playing "+game["title"]+" • use the game's own pause and save controls")
        self.root.iconify();self.refresh()

    def _pc_quit_menu(self,sid):
        self.menu("Quit this PC game?",[("Keep playing",lambda:self._resume(sid)),
            ("Request game close",lambda:self._stop(sid))],message="Save your progress in the game first. Redux will ask the game to close.")
        self.overlay["resume_id"]=sid
