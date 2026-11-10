"""Bounded read-only sector-method diagnostic; never capture or alter a disc."""
from datetime import datetime,timezone
import hashlib,json
from pathlib import Path
import sys,time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from redux.raw_acquisition import WindowsRawCdDevice,CDDA,RAW_WITH_SUBCODE,_data_mode
from redux.disc_identity import identify_drive


def main():
    drive=sys.argv[1] if len(sys.argv)>1 else "G:"
    record={"observed_at_utc":datetime.now(timezone.utc).isoformat(),"scope":"Bounded read-only queries; no tray/write/speed/capture/firmware action","drive":drive,"reads":[]}
    identity=identify_drive(drive)
    record["identity"]={"title":identity.title,"system":identity.system,"product":identity.game_id,"fingerprint":identity.fingerprint}
    with WindowsRawCdDevice(drive) as device:
        snapshot=device.snapshot();record.update(profile=snapshot.profile,counter=snapshot.media_change_count)
        for lba,mode in [(0,RAW_WITH_SUBCODE),(0,CDDA),(16,CDDA),(127008,CDDA),(127009,CDDA),(130740,CDDA)]:
            started=time.monotonic();item={"lba":lba,"mode":mode}
            try:
                data=device.read_sectors(lba,1,mode)
                item.update(bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),head_hex=data[:32].hex())
                try:item["data_mode"]=_data_mode(data,lba)
                except Exception:item["data_mode"]=None
            except Exception as error:item.update(error=str(error),winerror=getattr(error,"winerror",None),reason=getattr(error,"reason",None))
            item["seconds"]=round(time.monotonic()-started,3);record["reads"].append(item)
        record["same_media"]=device.snapshot()==snapshot
    folder=ROOT/"analysis/emulator_alignment";folder.mkdir(exist_ok=True)
    (folder/"raw-read-probe.json").write_text(json.dumps(record,indent=2),encoding="utf-8")
    print(json.dumps(record,indent=2))


if __name__=="__main__":main()
