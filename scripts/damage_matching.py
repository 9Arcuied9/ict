"""巡检档案匹配使用的距离、方向与外观规则。"""
import math

from .damage_appearance import compare_crops


def distance_m(lat, lon, other_lat, other_lon):
    a, b = map(math.radians, (lat, other_lat))
    h = math.sin((b-a)/2)**2 + math.cos(a)*math.cos(b)*math.sin(math.radians(other_lon-lon)/2)**2
    return 12742000 * math.asin(math.sqrt(min(1.0, h)))


def match_appearance(observation, reference, threshold, features):
    if reference['class'] != observation['class']:
        return None, 'class_conflict'
    side = observation.get('road_side')
    if side and reference.get('road_side') and side != reference['road_side']:
        return None, 'road_side_conflict'
    a, b = reference['heading'], observation['heading']
    if a is not None and b is not None and abs((a-b+180) % 360-180) > 60:
        return None, 'heading_conflict'
    score = compare_crops(features(observation['crop']), features(reference['crop']))
    if score is None:
        return None, 'insufficient_image_evidence'
    return score, 'strong' if score >= threshold else 'appearance_unconfirmed'
