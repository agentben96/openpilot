#!/usr/bin/env python3
import os
import sys
import multiprocessing
import subprocess
import argparse
import re
from tempfile import NamedTemporaryFile

from common.basedir import BASEDIR
from selfdrive.test.process_replay.compare_logs import save_log
from tools.lib.api import CommaApi
from tools.lib.auth_config import get_token
from tools.lib.robust_logreader import RobustLogReader
from tools.lib.route import Route, RouteSegmentName, SEGMENT_NAME_RE, ROUTE_NAME_RE
from urllib.parse import urlparse, parse_qs

juggle_dir = os.path.dirname(os.path.realpath(__file__))

DEMO_ROUTE = "4cf7a6ad03080c90|2021-09-29--13-46-36"

def get_route_and_segment_num(name):
  if re.fullmatch(SEGMENT_NAME_RE, name):
    rsn = RouteSegmentName(name)
    return rsn.route_name, rsn.segment_num
  if re.fullmatch(ROUTE_NAME_RE, name):
    return name, None
  raise Exception("invalid route or segment name:", name)

def load_segment(segment_name):
  print(f"Loading {segment_name}")
  if segment_name is None:
    return []

  try:
    return list(RobustLogReader(segment_name))
  except ValueError as e:
    print(f"Error parsing {segment_name}: {e}")
    return []

def start_juggler(fn=None, dbc=None, layout=None):
  env = os.environ.copy()
  env["BASEDIR"] = BASEDIR
  pj = os.getenv("PLOTJUGGLER_PATH", os.path.join(juggle_dir, "bin/plotjuggler"))

  if dbc:
    env["DBC_NAME"] = dbc

  extra_args = []
  if fn is not None:
    extra_args.append(f'-d {fn}')

  if layout is not None:
    extra_args.append(f'-l {layout}')

  extra_args = " ".join(extra_args)
  subprocess.call(f'{pj} --plugin_folders {os.path.join(juggle_dir, "bin")} {extra_args}', shell=True, env=env, cwd=juggle_dir)

def juggle_route(route_or_segment_name, segment_count, qlog, can, layout):
  segment_start = 0
  if 'cabana' in route_or_segment_name:
    query = parse_qs(urlparse(route_or_segment_name).query)
    api = CommaApi(get_token())
    logs = api.get(f'v1/route/{query["route"][0]}/log_urls?sig={query["sig"][0]}&exp={query["exp"][0]}')
  elif route_or_segment_name.startswith("http://") or route_or_segment_name.startswith("https://") or os.path.isfile(route_or_segment_name):
    logs = [route_or_segment_name]
  else:
    route_name, segment_number = get_route_and_segment_num(route_or_segment_name)
    segment_start = segment_number or 0
    if segment_number is not None and segment_count is None:
      segment_count = 1
    r = Route(route_name)
    logs = r.qlog_paths() if qlog else r.log_paths()

  segment_end = segment_start + segment_count if segment_count else -1
  logs = logs[segment_start:segment_end]

  if None in logs:
    fallback_answer = input("At least one of the rlogs in this segment does not exist, would you like to use the qlogs? (y/n) : ")
    if fallback_answer == 'y':
      logs = r.qlog_paths()[segment_start:segment_end]
    else:
      print("Please try a different route or segment")
      return

  all_data = []
  with multiprocessing.Pool(24) as pool:
    for d in pool.map(load_segment, logs):
      all_data += d

  if not can:
    all_data = [d for d in all_data if d.which() not in ['can', 'sendcan']]

  # Infer DBC name from logs
  dbc = None
  for cp in [m for m in all_data if m.which() == 'carParams']:
    try:
      DBC = __import__(f"selfdrive.car.{cp.carParams.carName}.values", fromlist=['DBC']).DBC
      dbc = DBC[cp.carParams.carFingerprint]['pt']
    except (ImportError, KeyError, AttributeError):
      pass
    break

  tempfile = NamedTemporaryFile(suffix='.rlog', dir=juggle_dir)
  save_log(tempfile.name, all_data, compress=False)
  del all_data

  start_juggler(tempfile.name, dbc, layout)

def get_arg_parser():
  parser = argparse.ArgumentParser(description="A helper to run PlotJuggler on openpilot routes",
                                   formatter_class=argparse.ArgumentDefaultsHelpFormatter)

  parser.add_argument("--demo", action="store_true", help="Use the demo route instead of providing one")
  parser.add_argument("--qlog", action="store_true", help="Use qlogs")
  parser.add_argument("--can", action="store_true", help="Parse CAN data")
  parser.add_argument("--stream", action="store_true", help="Start PlotJuggler in streaming mode")
  parser.add_argument("--layout", nargs='?', help="Run PlotJuggler with a pre-defined layout")
  parser.add_argument("route_or_segment_name", nargs='?', help="The route or segment name to plot (cabana share URL accepted)")
  parser.add_argument("segment_count", type=int, nargs='?', help="The number of segments to plot")
  return parser

if __name__ == "__main__":
  arg_parser = get_arg_parser()
  if len(sys.argv) == 1:
    arg_parser.print_help()
    sys.exit()
  args = arg_parser.parse_args(sys.argv[1:])

  if args.stream:
    start_juggler(layout=args.layout)
  else:
    route_or_segment_name = DEMO_ROUTE if args.demo else args.route_or_segment_name.strip()
    juggle_route(route_or_segment_name, args.segment_count, args.qlog, args.can, args.layout)
