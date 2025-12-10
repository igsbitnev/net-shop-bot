#!/usr/bin/env bash
export $(cat .env | xargs)
python bot.py
