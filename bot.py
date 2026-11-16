@dp.message(F.text == "✅ Завершить и отправить")
async def finish_and_send(message: types.Message):
    user_id = message.from_user.id
    tracks = get_user_tracks(user_id)
    if not tracks:
        await message.answer("Нет треков.")
        return
    prof = get_user_profile(user_id)
    if not prof:
        await message.answer("Профиль не найден.")
        return
    full_name, phone, address = prof[0], prof[1], prof[2] or ""

    # Короткое сообщение: только шапка и итог
    text = (
        f"📦 Новый заказ от {full_name}\n"
        f"👤 @{message.from_user.username or 'нет'}\n"
        f"📞 {phone}\n"
        f"🏠 {address}\n\n"
        f"📊 Всего треков: {len(tracks)}\n"
        f"💰 Итого: {get_total_sum_cny(user_id):.2f} CNY ≈ {get_total_sum_usd(user_id):.2f} USD ≈ {get_total_sum_byn(user_id):.2f} BYN"
    )

    excel_file = create_excel(tracks, full_name, phone, address, user_id)
    packing_file = create_packing_list(tracks)
    inventory_file = create_inventory_list(tracks, full_name, phone, address)

    try:
        # Отправляем короткое сообщение
        await bot.send_message(OWNER_ID, text)

        # Отправляем файлы
        await bot.send_document(OWNER_ID, BufferedInputFile(excel_file.getvalue(), filename=f"tracks_{user_id}.xlsx"), caption=f"Excel от {full_name}")
        await bot.send_document(OWNER_ID, BufferedInputFile(packing_file.getvalue(), filename=f"packing_{user_id}.xlsx"), caption=f"📦 Упаковочный лист от {full_name}")
        await bot.send_document(OWNER_ID, BufferedInputFile(inventory_file.getvalue(), filename=f"inventory_{user_id}.xlsx"), caption=f"📋 Опись от {full_name}")

        # Сообщение клиенту
        await message.answer("✅ Ваши треки отправлены владельцу!")
    except Exception as e:
        await message.answer(f"Ошибка: {e}")
